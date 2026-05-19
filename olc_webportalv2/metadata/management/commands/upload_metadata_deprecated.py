"""
This script updates the metadata tables in a Django application using two CSV
files: SeqTracking.csv and SeqMetadata.csv. The script reads the CSV files,
processes the data, and updates the database accordingly. It also handles
bulk creation and updates of database entries to improve performance.

Usage:
    python manage.py <command_name> <path_to_SeqTracking.csv>
    <path_to_SeqMetadata.csv>
"""

import csv
import logging
import re
import traceback
from typing import Dict, List, Set

from azure.storage.blob import BlockBlobService
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from olc_webportalv2.metadata.models import (
    Genus,
    LabID,
    MLST,
    OLNID,
    RMLST,
    SequenceData,
    Serotype,
    Species,
)

logger = logging.getLogger(__name__)


class DuplicateEntryError(Exception):
    pass


class DataToUpload:
    """
    Class to hold data that needs to be uploaded.
    """

    def __init__(self, seqid):
        self.seqid = seqid  # type: str
        self.labid = None  # type: str
        self.mlst = None  # type: str
        self.rmlst = None  # type: str
        self.genus = None  # type: str
        self.species = None  # type: str
        self.quality = None  # type: str
        self.serotype = None  # type: str
        self.olnid = None  # type: str


def initialize_blob_client() -> BlockBlobService:
    """
    Initializes the Azure Blob Service client.

    :return: BlockBlobService instance.
    """
    return BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )


def get_seqids_in_cloud(blob_client: BlockBlobService) -> Set[str]:
    """
    Retrieves the list of sequence IDs stored in the cloud.

    :param blob_client: BlockBlobService instance.
    :return: Set of sequence IDs.
    """
    return set(
        blob.name.replace('.fasta', '') for blob in
        blob_client.list_blobs('processed-data')
    )


def get_existing_attributes() -> Dict[str, Set[str]]:
    """
    Retrieves existing attributes from the database.

    :return: Dictionary of existing attributes.
    """
    return {
        'genera': set(Genus.objects.values_list('genus', flat=True)),
        'species': set(Species.objects.values_list('species', flat=True)),
        'serotypes': set(Serotype.objects.values_list('serotype', flat=True)),
        'mlst': set(MLST.objects.values_list('mlst', flat=True)),
        'rmlst': set(RMLST.objects.values_list('rmlst', flat=True)),
        'olnid': set(OLNID.objects.values_list('olnid', flat=True)),
        'labid': set(LabID.objects.values_list('labid', flat=True))
    }


def parse_seqtracking_csv(seqtracking_csv: str) -> Dict[str, DataToUpload]:
    """
    Parses the SeqTracking CSV file and creates a dictionary of DataToUpload
    objects.

    :param seqtracking_csv: Path to the SeqTracking.csv file.
    :return: Dictionary of DataToUpload objects.
    """
    seqdata_dict = {}
    with open(seqtracking_csv, encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            seqid = row['SEQID']
            quality = row['CuratorFlag'].upper()
            labid = row['LabID']
            genus = row['Genus']
            species = row['Species']
            serotype = row['Serotype']
            olnid = row['OLNID'].upper()
            seqdata = DataToUpload(seqid)

            # Set quality attribute
            if 'REFERENCE' in quality:
                seqdata.quality = 'Reference'
            elif 'PASS' in quality:
                seqdata.quality = 'Pass'
            else:
                seqdata.quality = 'Fail'

            # Check if LabID is acceptable and set it
            if re.fullmatch(
                    '[A-Z]{3}-[A-Z]{2}-\\d{4}-[A-Z]{2,3}-\\d{4,5}', labid):
                seqdata.labid = labid

            # Set OLNID if it matches the pattern
            if 'OLF' in olnid or 'OLC' in olnid:
                seqdata.olnid = olnid

            # Set genus, species, and serotype
            seqdata.genus = genus.lower().capitalize()
            seqdata.serotype = serotype.lower().capitalize()
            seqdata.species = species.lower() if species else 'ND'

            seqdata_dict[seqid] = seqdata
    return seqdata_dict


def update_seqdata_with_metadata(
        seqmetadata_csv: str,
        seqdata_dict: Dict[str, DataToUpload]) -> None:
    """
    Updates the DataToUpload objects with metadata from the SeqMetadata CSV
    file.

    :param seqmetadata_csv: Path to the SeqMetadata.csv file.
    :param seqdata_dict: Dictionary of DataToUpload objects.
    """
    with open(seqmetadata_csv, encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            seqid = row['SeqID']
            mlst = row['MLST_Result'] if row['MLST_Result'] else 'ND'
            rmlst = row['rMLST_Result'] if row['rMLST_Result'] else 'ND'

            if seqid in seqdata_dict:
                seqdata_dict[seqid].mlst = mlst
                seqdata_dict[seqid].rmlst = rmlst


def prepare_bulk_operations(
        seqdata_dict: Dict[str, DataToUpload],
        seqids_in_cloud: Set[str],
        existing_attributes: Dict[str, Set[str]]) -> Dict[str, List]:
    """
    Prepares the data for bulk creation and updates.

    :param seqdata_dict: Dictionary of DataToUpload objects.
    :param seqids_in_cloud: Set of sequence IDs stored in the cloud.
    :param existing_attributes: Dictionary of existing attributes.
    :return: Dictionary of bulk operations.
    """
    sequence_data_to_create = []
    sequence_data_to_update = []
    genus_to_create = set()
    species_to_create = set()
    serotype_to_create = set()
    mlst_to_create = set()
    rmlst_to_create = set()
    labid_to_create = set()
    olnid_to_create = set()

    for seqid, seqdata in seqdata_dict.items():
        if seqid not in seqids_in_cloud:
            continue

        # Check and prepare new genera, species, serotypes, mlst, and rmlst for
        # bulk creation
        if seqdata.genus not in existing_attributes['genera']:
            genus_to_create.add(seqdata.genus)
            existing_attributes['genera'].add(seqdata.genus)

        if seqdata.species not in existing_attributes['species']:
            species_to_create.add(seqdata.species)
            existing_attributes['species'].add(seqdata.species)

        if seqdata.serotype not in existing_attributes['serotypes']:
            serotype_to_create.add(seqdata.serotype)
            existing_attributes['serotypes'].add(seqdata.serotype)

        # Ensure MLST is not null and convert to 'ND' if necessary
        if not seqdata.mlst or seqdata.mlst == 'ND':
            seqdata.mlst = 'ND'
        if seqdata.mlst not in existing_attributes['mlst']:
            mlst_to_create.add(seqdata.mlst)
            existing_attributes['mlst'].add(seqdata.mlst)

        # Ensure rMLST is not null and convert to 'ND' if necessary
        if not seqdata.rmlst or seqdata.rmlst == 'ND':
            seqdata.rmlst = 'ND'
        if seqdata.rmlst not in existing_attributes['rmlst']:
            rmlst_to_create.add(seqdata.rmlst)
            existing_attributes['rmlst'].add(seqdata.rmlst)

        # Prepare LabID and OLNID for bulk creation
        if seqdata.labid:
            labid_to_create.add(seqdata.labid)
        if seqdata.olnid:
            olnid_to_create.add(seqdata.olnid)

        # Prepare SequenceData for bulk creation or update
        if not SequenceData.objects.filter(seqid=seqid).exists():
            sequence_data_to_create.append(seqdata)
        else:
            sequence_data_to_update.append(seqdata)

    return {
        'sequence_data_to_create': sequence_data_to_create,
        'sequence_data_to_update': sequence_data_to_update,
        'genus_to_create': genus_to_create,
        'species_to_create': species_to_create,
        'serotype_to_create': serotype_to_create,
        'mlst_to_create': mlst_to_create,
        'rmlst_to_create': rmlst_to_create,
        'labid_to_create': labid_to_create,
        'olnid_to_create': olnid_to_create
    }


def bulk_create_entries(bulk_operations: Dict[str, List]) -> None:
    """
    Performs bulk creation of new entries.

    :param bulk_operations: Dictionary of bulk operations.
    """
    with transaction.atomic():
        Genus.objects.bulk_create(
            [Genus(genus=g) for g in bulk_operations['genus_to_create']]
        )
        Species.objects.bulk_create(
            [Species(species=species)
             for species in bulk_operations['species_to_create']]
        )
        Serotype.objects.bulk_create(
            [Serotype(serotype=sero)
             for sero in bulk_operations['serotype_to_create']]
        )
        MLST.objects.bulk_create(
            [
                MLST(mlst=mlst) for mlst in bulk_operations['mlst_to_create']
            ]
        )
        RMLST.objects.bulk_create(
            [
                RMLST(rmlst=rmlst)
                for rmlst in bulk_operations['rmlst_to_create']
            ]
        )
        LabID.objects.bulk_create([LabID(labid=labid)
                                   for labid in bulk_operations
                                   ['labid_to_create']])
        OLNID.objects.bulk_create([OLNID(olnid=olnid)
                                   for olnid in bulk_operations
                                   ['olnid_to_create']])

        # Create a mapping of labid and olnid strings to their instances
        labid_map = {labid.labid: labid for labid in LabID.objects.all()}
        olnid_map = {olnid.olnid: olnid for olnid in OLNID.objects.all()}

        # Update SequenceData with the correct LabID and OLNID instances
        sequence_data_to_create = []
        for seqdata in bulk_operations['sequence_data_to_create']:
            sequence_data_to_create.append(
                SequenceData(
                    seqid=seqdata.seqid,
                    quality=seqdata.quality,
                    genus=seqdata.genus,
                    species=seqdata.species,
                    serotype=seqdata.serotype,
                    mlst=seqdata.mlst,
                    rmlst=seqdata.rmlst,
                    labid=labid_map.get(seqdata.labid),
                    olnid=olnid_map.get(seqdata.olnid)
                )
            )

        SequenceData.objects.bulk_create(sequence_data_to_create)


def update_existing_entries(
        sequence_data_to_update: List[DataToUpload]) -> None:
    """
    Updates existing entries individually.

    :param sequence_data_to_update: List of DataToUpload objects to update.
    """
    for seqdata in sequence_data_to_update:
        sequence_data = SequenceData.objects.get(seqid=seqdata.seqid)
        sequence_data.quality = seqdata.quality
        sequence_data.genus = seqdata.genus
        sequence_data.species = seqdata.species
        sequence_data.serotype = seqdata.serotype
        sequence_data.mlst = seqdata.mlst
        sequence_data.rmlst = seqdata.rmlst
        sequence_data.save()

        if seqdata.labid:
            try:
                lab_data, _ = LabID.objects.get_or_create(
                    labid=seqdata.labid)
                sequence_data.labid = lab_data
                sequence_data.save()
            except LabID.MultipleObjectsReturned:
                logger.error("Duplicate LabID found: %s", seqdata.labid)
                continue

        if seqdata.olnid:
            try:
                oln_data, _ = OLNID.objects.get_or_create(
                    olnid=seqdata.olnid)
                sequence_data.olnid = oln_data
                sequence_data.save()
            except OLNID.MultipleObjectsReturned:
                logger.error("Duplicate OLNID found: %s", seqdata.olnid)
                continue


def find_missing_seqids(
        seqtracking_csv: str,
        seqmetadata_csv: str) -> None:
    """
    Finds and prints seqids that are present in the database but not in the
    provided CSV files.

    :param seqtracking_csv: Path to the SeqTracking.csv file.
    :param seqmetadata_csv: Path to the SeqMetadata.csv file.
    """
    # Read seqids from seqtracking_csv
    seqids_in_tracking = set()
    with open(seqtracking_csv, encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            seqids_in_tracking.add(row['SEQID'])

    # Read seqids from seqmetadata_csv
    seqids_in_metadata = set()
    with open(seqmetadata_csv, encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            seqids_in_metadata.add(row['SeqID'])  # Note the lowercase 'q'

    # Combine seqids from both CSV files
    seqids_in_csv = seqids_in_tracking.union(seqids_in_metadata)

    # Get seqids from the database
    seqids_in_db = set(SequenceData.objects.values_list('seqid', flat=True))

    # Find seqids that are in the database but not in the CSV files
    missing_seqids = seqids_in_db - seqids_in_csv

    if missing_seqids:
        print("SeqIDs present in the database but not in the CSV files:")
        for seqid in missing_seqids:
            print(seqid)
    else:
        print("No missing SeqIDs found.")


def clear_all_entries() -> None:
    """
    Clears all entries in the Genus, Species, Serotype, MLST, RMLST,
    SequenceData, LabID, and OLNID models.
    """
    # Clear entries in Genus model
    Genus.objects.all().delete()
    print("Cleared all entries in Genus model.")

    # Clear entries in Species model
    Species.objects.all().delete()
    print("Cleared all entries in Species model.")

    # Clear entries in Serotype model
    Serotype.objects.all().delete()
    print("Cleared all entries in Serotype model.")

    # Clear entries in MLST model
    MLST.objects.all().delete()
    print("Cleared all entries in MLST model.")

    # Clear entries in RMLST model
    RMLST.objects.all().delete()
    print("Cleared all entries in RMLST model.")

    # Clear entries in SequenceData model
    SequenceData.objects.all().delete()
    print("Cleared all entries in SequenceData model.")

    # Clear entries in LabID model
    LabID.objects.all().delete()
    print("Cleared all entries in LabID model.")

    # Clear entries in OLNID model
    OLNID.objects.all().delete()
    print("Cleared all entries in OLNID model.")


def upload_metadata(
        seqtracking_csv: str,
        seqmetadata_csv: str,
        clear_all: bool = False) -> None:
    """
    Uploads metadata from the given CSV files to the database.

    :param seqtracking_csv: Path to the SeqTracking.csv file.
    :param seqmetadata_csv: Path to the SeqMetadata.csv file.
    :param clear_all: Boolean flag to clear all existing entries before upload.
    """
    logger.info("Starting metadata upload process.")

    if clear_all:
        # Clear all existing entries
        logger.info("Clearing all existing entries in the database.")
        clear_all_entries()

    # Initialize Azure Blob Service client
    logger.info("Initializing Azure Blob Service client.")
    blob_client = initialize_blob_client()

    # Get the list of sequence IDs stored in the cloud
    logger.info("Retrieving sequence IDs stored in the cloud.")
    seqids_in_cloud = get_seqids_in_cloud(blob_client)

    # Get existing attributes from the database
    logger.info("Retrieving existing attributes from the database.")
    existing_attributes = get_existing_attributes()

    # Parse SeqTracking CSV file
    logger.info("Parsing SeqTracking CSV file: %s", seqtracking_csv)
    seqdata_dict = parse_seqtracking_csv(seqtracking_csv)

    # Update SeqData with metadata from SeqMetadata CSV file
    logger.info("Updating SeqData with metadata from SeqMetadata CSV file: %s",
                seqmetadata_csv)
    update_seqdata_with_metadata(seqmetadata_csv, seqdata_dict)

    # Prepare bulk operations
    logger.info("Preparing bulk operations.")
    bulk_operations = prepare_bulk_operations(
        seqdata_dict, seqids_in_cloud, existing_attributes)

    # Perform bulk creation of new entries
    logger.info("Performing bulk creation of new entries.")
    bulk_create_entries(bulk_operations)

    logger.info("Metadata upload process completed successfully.")
    # Uncomment if needed
    # update_existing_entries(bulk_operations['sequence_data_to_update'])


class Command(BaseCommand):
    """
    Django management command to update metadata from CSV files.
    """
    help = (
        'Updates metadata using a SeqTracking.csv and SeqMetadata.csv from '
        'the OLC Access database.')

    def add_arguments(self, parser) -> None:
        """
        Adds arguments to the command.
        """
        parser.add_argument(
            'seqtracking_csv',
            type=str,
            help='Path to SeqTracking.csv exported from OLC Access Database.'
        )
        parser.add_argument(
            'seqmetadata_csv',
            type=str,
            help='Path to SeqMetadata.csv exported from OLC Access Database.'
        )
        parser.add_argument(
            '-c', '--clear',
            action='store_true',
            help='Clear all existing entries in the database before upload.'
        )

    def handle(self, **options) -> None:
        """
        Handles the command execution.
        """
        try:
            upload_metadata(
                seqtracking_csv=options['seqtracking_csv'],
                seqmetadata_csv=options['seqmetadata_csv'],
                clear_all=options['clear']
            )
        except DuplicateEntryError as exc:
            logger.error("An error occurred: %s", exc)
            logger.error(traceback.format_exc())
        except Exception as exc:
            logger.error("An error occurred: %s", exc)
            logger.error(traceback.format_exc())
