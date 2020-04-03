from django.core.management.base import BaseCommand
from olc_webportalv2.metadata.models import SequenceData, LabID, Genus, Species, Serotype, MLST, RMLST, OLNID
from django.conf import settings
import csv
import re

from azure.storage.blob import BlockBlobService


def upload_metadata(seqtracking_csv, seqmetadata_csv):
    blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                   account_key=settings.AZURE_ACCOUNT_KEY)
    seqids_in_cloud = list()
    generator = blob_client.list_blobs('processed-data')
    for blob in generator:
        seqids_in_cloud.append(blob.name.replace('.fasta', ''))

    seqdata_dict = dict()  # This stores all of our DataToUpload objects, accessed with SeqID keys.

    # Make lists of attributes we have.
    genera_present = [str(genus) for genus in Genus.objects.filter()]
    species_present = [str(species) for species in Species.objects.filter()]
    serotypes_present = [str(serotype) for serotype in Serotype.objects.filter()]
    mlst_present = [str(mlst) for mlst in MLST.objects.filter()]
    rmlst_present = [str(rmlst) for rmlst in RMLST.objects.filter()]

    # First up: Make a pass through seqtracking to pull out SeqIDs, LabIDs (if the SeqID has one), genus, species,
    # quality, and serotype.
    with open(seqtracking_csv) as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # Pull out variable so we don't have to look at ugly syntax
            seqid = row['SEQID']
            quality = row['CuratorFlag'].upper()
            labid = row['LabID']
            genus = row['Genus']
            species = row['Species']
            serotype = row['Serotype']
            olnid = row['OLNID'].upper()
            seqdata = DataToUpload(seqid)
            
            # Set quality attribute.
            if 'REFERENCE' in quality:
                seqdata.quality = 'Reference'
            elif 'PASS' in quality:
                seqdata.quality = 'Pass'
            else:
                seqdata.quality = 'Fail'
            
            # Check if our LabID looks acceptable. If yes, set the labid attr of seqdata_dict
            if re.fullmatch('[A-Z]{3}-[A-Z]{2}-\d{4}-[A-Z]{2,3}-\d{4,5}', labid):
                seqdata.labid = labid

            if 'OLF' in olnid or 'OLC' in olnid:
                seqdata.olnid = olnid
                 
            # Pull out genus, species, and serotype. Genus/serotype should have first char uppercase, species should
            # be entirely lowercase. # TODO: Not sure what happens with a blank value. 
            seqdata.genus = genus.lower().capitalize()
            seqdata.serotype = serotype.lower().capitalize()
            seqdata.species = species.lower() if species else 'ND'
            
            seqdata_dict[seqid] = seqdata

    # Now go through seqmetadata to get mlst and rmlst updated
    with open(seqmetadata_csv) as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            seqid = row['SeqID']
            mlst = row['MLST_Result']
            rmlst = row['rMLST_Result']
            if seqid in seqdata_dict:
                seqdata_dict[seqid].mlst = mlst
                seqdata_dict[seqid].rmlst = rmlst

    # Now add the metadata to our database!
    for seqid in seqdata_dict:
        # If we don't have actual sequence data associated, don't upload metadata.
        if seqid not in seqids_in_cloud:
            print('WARNING: SeqID {} was listed on metadata sheet, but is not stored in cloud.'.format(seqid))
            continue

        # Check if we need to add to our lists of Genera, Species, Serotype, MLST, RMLST for autocompletion
        if seqdata_dict[seqid].genus not in genera_present:
            Genus.objects.create(genus=seqdata_dict[seqid].genus)
            genera_present.append(seqdata_dict[seqid].genus)

        if seqdata_dict[seqid].species not in species_present:
            Species.objects.create(species=seqdata_dict[seqid].species)
            species_present.append(seqdata_dict[seqid].species)

        if seqdata_dict[seqid].serotype not in serotypes_present:
            Serotype.objects.create(serotype=seqdata_dict[seqid].serotype)
            serotypes_present.append(seqdata_dict[seqid].serotype)

        if seqdata_dict[seqid].mlst not in mlst_present:
            MLST.objects.create(mlst=seqdata_dict[seqid].mlst)
            mlst_present.append(seqdata_dict[seqid].mlst)

        if seqdata_dict[seqid].rmlst not in rmlst_present:
            RMLST.objects.create(rmlst=seqdata_dict[seqid].rmlst)
            rmlst_present.append(seqdata_dict[seqid].rmlst)

        # Create a SequenceData object, if needed. Otherwise, update!
        if not SequenceData.objects.filter(seqid=seqid).exists():
            SequenceData.objects.create(seqid=seqid,
                                        quality=seqdata_dict[seqid].quality,
                                        genus=seqdata_dict[seqid].genus,
                                        species=seqdata_dict[seqid].species,
                                        serotype=seqdata_dict[seqid].serotype,
                                        mlst=seqdata_dict[seqid].mlst,
                                        rmlst=seqdata_dict[seqid].rmlst)
        else:
            sequence_data = SequenceData.objects.get(seqid=seqid)
            sequence_data.quality = seqdata_dict[seqid].quality
            sequence_data.genus = seqdata_dict[seqid].genus
            sequence_data.species = seqdata_dict[seqid].species
            sequence_data.serotype = seqdata_dict[seqid].serotype
            sequence_data.mlst = seqdata_dict[seqid].mlst
            sequence_data.rmlst = seqdata_dict[seqid].rmlst
            sequence_data.save()
            
        # Check if our seqdata has a labID. If it does, create labid object if necessary, and then link the SequenceData
        # back to the labID
        if seqdata_dict[seqid].labid is not None:
            if not LabID.objects.filter(labid=seqdata_dict[seqid].labid).exists():
                LabID.objects.create(labid=seqdata_dict[seqid].labid)
            sequence_data = SequenceData.objects.get(seqid=seqid)
            lab_data = LabID.objects.get(labid=seqdata_dict[seqid].labid)
            sequence_data.labid = lab_data
            sequence_data.save()
        # Also check for some sort of OLN ID.
        if seqdata_dict[seqid].olnid is not None:
            try:
                if not OLNID.objects.filter(olnid=seqdata_dict[seqid].olnid).exists():
                    OLNID.objects.create(olnid=seqdata_dict[seqid].olnid)
                sequence_data = SequenceData.objects.get(seqid=seqid)
                lab_data = OLNID.objects.get(olnid=seqdata_dict[seqid].olnid)
                sequence_data.olnid = lab_data
                sequence_data.save()
            except:  # Some OLN IDs are too long for the DB field I've set up to handle. Skip over those.
                pass


class DataToUpload:
    # Class (but actually kinda a struct) that holds data we need to upload.
    def __init__(self, seqid):
        self.seqid = seqid
        self.labid = None
        self.mlst = None
        self.rmlst = None
        self.genus = None
        self.species = None
        self.quality = None
        self.serotype = None
        self.olnid = None


class Command(BaseCommand):
    help = 'Updates metadata when given a SeqTracking.csv and SeqMetadata.csv from OLC Access database.' 

    def add_arguments(self, parser):
        parser.add_argument('seqtracking_csv',
                            type=str,
                            help='Full path to SeqTracking.csv exported from OLC Access Database.')
        parser.add_argument('seqmetadata_csv',
                            type=str,
                            help='Full path to SeqMetadata.csv exported from OLC Access Database.')

    def handle(self, *args, **options):
        upload_metadata(seqtracking_csv=options['seqtracking_csv'],
                        seqmetadata_csv=options['seqmetadata_csv'])
