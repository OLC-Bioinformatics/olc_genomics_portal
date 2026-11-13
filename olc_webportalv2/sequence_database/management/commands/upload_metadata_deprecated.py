"""Legacy management command for importing metadata CSV files."""

import csv
import re

from django.core.management.base import BaseCommand

from olc_webportalv2.common.storage_client import get_storage_client
from olc_webportalv2.metadata.models import (
    MLST,
    OLNID,
    RMLST,
    Genus,
    LabID,
    SequenceData,
    Serotype,
    Species,
)


def upload_metadata(seqtracking_csv, seqmetadata_csv):
    """Upload metadata from SeqTracking and SeqMetadata CSV exports.

    Args:
        seqtracking_csv: Path to the SeqTracking CSV file.
        seqmetadata_csv: Path to the SeqMetadata CSV file.
    """
    blob_client = get_storage_client()
    seqids_in_cloud = []
    generator = blob_client.list_objects('processed-data')
    for blob in generator:
        seqids_in_cloud.append(blob.name.replace('.fasta', ''))

    # Store upload records by sequence identifier.
    seqdata_dict = {}

    # Make lists of attributes we have.
    genera_present = [str(genus) for genus in Genus.objects.filter()]
    species_present = [str(species) for species in Species.objects.filter()]
    serotypes_present = [
        str(serotype) for serotype in Serotype.objects.filter()
    ]
    mlst_present = [str(mlst) for mlst in MLST.objects.filter()]
    rmlst_present = [str(rmlst) for rmlst in RMLST.objects.filter()]

    # Read sequence identifiers, taxonomy, quality, and sample identifiers.
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

            # Keep only LabIDs matching the expected format.
            if re.fullmatch(
                '[A-Z]{3}-[A-Z]{2}-\d{4}-[A-Z]{2,3}-\d{4,5}', labid
            ):
                seqdata.labid = labid

            if 'OLF' in olnid or 'OLC' in olnid:
                seqdata.olnid = olnid

            # Normalize genus, species, and serotype casing.
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
        # Do not upload metadata without a stored sequence.
        if seqid not in seqids_in_cloud:
            print(
                f'WARNING: SeqID {seqid} has metadata but no stored sequence.'
            )
            continue

        # Add new values to the autocomplete tables.
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
            SequenceData.objects.create(
                seqid=seqid,
                quality=seqdata_dict[seqid].quality,
                genus=seqdata_dict[seqid].genus,
                species=seqdata_dict[seqid].species,
                serotype=seqdata_dict[seqid].serotype,
                mlst=seqdata_dict[seqid].mlst,
                rmlst=seqdata_dict[seqid].rmlst,
            )
        else:
            sequence_data = SequenceData.objects.get(seqid=seqid)
            sequence_data.quality = seqdata_dict[seqid].quality
            sequence_data.genus = seqdata_dict[seqid].genus
            sequence_data.species = seqdata_dict[seqid].species
            sequence_data.serotype = seqdata_dict[seqid].serotype
            sequence_data.mlst = seqdata_dict[seqid].mlst
            sequence_data.rmlst = seqdata_dict[seqid].rmlst
            sequence_data.save()

        # Link the sequence to its LabID when one is available.
        if seqdata_dict[seqid].labid is not None:
            if not LabID.objects.filter(
                labid=seqdata_dict[seqid].labid
            ).exists():
                LabID.objects.create(labid=seqdata_dict[seqid].labid)
            sequence_data = SequenceData.objects.get(seqid=seqid)
            lab_data = LabID.objects.get(labid=seqdata_dict[seqid].labid)
            sequence_data.labid = lab_data
            sequence_data.save()
        # Also check for some sort of OLN ID.
        if seqdata_dict[seqid].olnid is not None:
            olnid = seqdata_dict[seqid].olnid
            max_length = OLNID._meta.get_field('olnid').max_length
            if len(olnid) > max_length:
                print(f'WARNING: Skipping overlong OLN ID {olnid!r}.')
                continue
            OLNID.objects.get_or_create(olnid=olnid)
            sequence_data = SequenceData.objects.get(seqid=seqid)
            sequence_data.olnid = OLNID.objects.get(olnid=olnid)
            sequence_data.save()


class DataToUpload:
    """Hold metadata values parsed from the CSV files."""

    def __init__(self, seqid):
        """Initialize an empty upload record.

        Args:
            seqid: The sequence identifier for this record.
        """
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
    """Expose the legacy CSV metadata importer as a Django command."""

    help = 'Update metadata from SeqTracking.csv and SeqMetadata.csv exports.'

    def add_arguments(self, parser):
        """Register required CSV path arguments.

        Args:
            parser: The command-line argument parser.
        """
        parser.add_argument(
            'seqtracking_csv',
            type=str,
            help='Path to SeqTracking.csv exported from OLC Access.',
        )
        parser.add_argument(
            'seqmetadata_csv',
            type=str,
            help='Path to SeqMetadata.csv exported from OLC Access.',
        )

    def handle(self, *args, **options):
        """Run the metadata importer with parsed command options.

        Args:
            args: Additional positional command arguments.
            options: Parsed command-line options.
        """
        upload_metadata(
            seqtracking_csv=options['seqtracking_csv'],
            seqmetadata_csv=options['seqmetadata_csv'],
        )
