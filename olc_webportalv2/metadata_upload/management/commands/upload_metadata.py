"""Import and update metadata from SeqTracking and SeqMetadata CSV files.

The importer parses both files, then creates or updates records in bulk.
"""

import csv
import logging
from typing import Dict, List, Set

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from olc_webportalv2.common.storage_client import (
    StorageClient,
    get_storage_client,
)
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

logger = logging.getLogger(__name__)


class DuplicateEntryError(Exception):
    """Raised when an import file contains a duplicate sequence identifier."""

    pass


class DataToUpload:
    """Hold metadata values parsed from the CSV files."""

    def __init__(self, seqid):
        """Initialize an empty upload record.

        Args:
            seqid: The sequence identifier for this record.

        """
        self.seqid = seqid  # type: str
        self.labid = None  # type: str
        self.mlst = None  # type: str
        self.rmlst = None  # type: str
        self.genus = None  # type: str
        self.species = None  # type: str
        self.quality = None  # type: str
        self.serotype = None  # type: str
        self.olnid = None  # type: str


def initialize_blob_client() -> StorageClient:
    """Create the provider-neutral storage client.

    Returns:
        The configured storage client.

    """
    return get_storage_client()


def get_seqids_in_cloud(blob_client: StorageClient) -> Set[str]:
    """Return sequence IDs for FASTA objects in processed storage.

    Args:
        blob_client: Storage client used to list processed data.

    Returns:
        Sequence IDs derived from ``.fasta`` object names.

    """
    return {
        blob.name[: -len('.fasta')]
        for blob in blob_client.list_objects('processed-data')
        if blob.name.endswith('.fasta')
    }


def get_existing_attributes() -> Dict[str, Set[str]]:
    """Collect existing values used to avoid duplicate metadata rows.

    Returns:
        A mapping of model fields to their stored values.

    """
    return {
        'genera': set(Genus.objects.values_list('genus', flat=True)),
        'species': set(Species.objects.values_list('species', flat=True)),
        'serotypes': set(Serotype.objects.values_list('serotype', flat=True)),
        'mlst': set(MLST.objects.values_list('mlst', flat=True)),
        'rmlst': set(RMLST.objects.values_list('rmlst', flat=True)),
        'olnid': set(OLNID.objects.values_list('olnid', flat=True)),
        'labid': set(LabID.objects.values_list('labid', flat=True)),
    }


def parse_seqtracking_csv(seqtracking_csv: str) -> Dict[str, DataToUpload]:
    """Parse SeqTracking rows into upload records.

    Args:
        seqtracking_csv: Path to the SeqTracking CSV file.

    Returns:
        Upload records keyed by sequence ID.

    Raises:
        DuplicateEntryError: If a sequence ID appears more than once.

    """
    seqdata_dict = {}
    with open(seqtracking_csv, encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            seqid = row['SEQID']
            if seqid in seqdata_dict:
                raise DuplicateEntryError(
                    f'Duplicate sequence ID in SeqTracking CSV: {seqid}'
                )
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

            seqdata.labid = labid
            seqdata.olnid = olnid

            # Set genus, species, and serotype
            seqdata.genus = genus.lower().capitalize()
            seqdata.serotype = serotype.lower().capitalize()
            seqdata.species = species.lower() if species else 'ND'

            seqdata_dict[seqid] = seqdata
    return seqdata_dict


def update_seqdata_with_metadata(
    seqmetadata_csv: str, seqdata_dict: Dict[str, DataToUpload]
) -> None:
    """Add MLST results from SeqMetadata rows to upload records.

    Args:
        seqmetadata_csv: Path to the SeqMetadata CSV file.
        seqdata_dict: Upload records keyed by sequence ID.

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
    existing_attributes: Dict[str, Set[str]],
) -> Dict[str, List]:
    """Partition cloud-backed records and collect new metadata values.

    Args:
        seqdata_dict: Upload records keyed by sequence ID.
        seqids_in_cloud: Sequence IDs with stored FASTA objects.
        existing_attributes: Existing metadata values by model field.

    Returns:
        Collections of sequence records to create or update and attributes
        that need new database rows.

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
    existing_seqids = set(
        SequenceData.objects.filter(seqid__in=seqdata_dict).values_list(
            'seqid', flat=True
        )
    )

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

        if seqdata.labid and seqdata.labid not in existing_attributes['labid']:
            labid_to_create.add(seqdata.labid)
            existing_attributes['labid'].add(seqdata.labid)
        if seqdata.olnid and seqdata.olnid not in existing_attributes['olnid']:
            olnid_to_create.add(seqdata.olnid)
            existing_attributes['olnid'].add(seqdata.olnid)

        if seqid not in existing_seqids:
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
        'olnid_to_create': olnid_to_create,
    }


def bulk_create_entries(bulk_operations: Dict[str, List]) -> None:
    """Create new metadata rows and their sequence records.

    Args:
        bulk_operations: Collections produced by
            :func:`prepare_bulk_operations`.

    """
    with transaction.atomic():
        Genus.objects.bulk_create(
            [Genus(genus=g) for g in bulk_operations['genus_to_create']]
        )
        Species.objects.bulk_create(
            [
                Species(species=species)
                for species in bulk_operations['species_to_create']
            ]
        )
        Serotype.objects.bulk_create(
            [
                Serotype(serotype=sero)
                for sero in bulk_operations['serotype_to_create']
            ]
        )
        MLST.objects.bulk_create(
            [MLST(mlst=mlst) for mlst in bulk_operations['mlst_to_create']]
        )
        RMLST.objects.bulk_create(
            [
                RMLST(rmlst=rmlst)
                for rmlst in bulk_operations['rmlst_to_create']
            ]
        )
        LabID.objects.bulk_create(
            [
                LabID(labid=labid)
                for labid in bulk_operations['labid_to_create']
            ]
        )
        OLNID.objects.bulk_create(
            [
                OLNID(olnid=olnid)
                for olnid in bulk_operations['olnid_to_create']
            ]
        )

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
                    olnid=olnid_map.get(seqdata.olnid),
                )
            )

        SequenceData.objects.bulk_create(sequence_data_to_create)


def update_existing_entries(
    sequence_data_to_update: List[DataToUpload],
) -> None:
    """Update stored sequence rows from imported metadata.

    Args:
        sequence_data_to_update: Upload records for existing sequence IDs.

    """
    for seqdata in sequence_data_to_update:
        sequence_data = SequenceData.objects.get(seqid=seqdata.seqid)
        sequence_data.quality = seqdata.quality
        sequence_data.genus = seqdata.genus
        sequence_data.species = seqdata.species
        sequence_data.serotype = seqdata.serotype
        sequence_data.mlst = seqdata.mlst
        sequence_data.rmlst = seqdata.rmlst

        if seqdata.labid:
            sequence_data.labid, _ = LabID.objects.get_or_create(
                labid=seqdata.labid
            )

        if seqdata.olnid:
            sequence_data.olnid, _ = OLNID.objects.get_or_create(
                olnid=seqdata.olnid
            )
        sequence_data.save()


def find_missing_seqids(seqtracking_csv: str, seqmetadata_csv: str) -> None:
    """Log database sequence IDs absent from both CSV files.

    Args:
        seqtracking_csv: Path to the SeqTracking CSV file.
        seqmetadata_csv: Path to the SeqMetadata CSV file.

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
        logger.info(
            'Sequence IDs absent from the CSV files: %s',
            ', '.join(sorted(missing_seqids)),
        )
    else:
        logger.info('No missing sequence IDs found.')


@transaction.atomic
def clear_all_entries() -> None:
    """Delete all metadata rows managed by the importer."""
    Genus.objects.all().delete()
    Species.objects.all().delete()
    Serotype.objects.all().delete()
    MLST.objects.all().delete()
    RMLST.objects.all().delete()
    SequenceData.objects.all().delete()
    LabID.objects.all().delete()
    OLNID.objects.all().delete()


def upload_metadata(
    seqtracking_csv: str, seqmetadata_csv: str, clear_all: bool = False
) -> None:
    """Import CSV metadata for sequences whose FASTA files are in storage.

    Args:
        seqtracking_csv: Path to the SeqTracking CSV file.
        seqmetadata_csv: Path to the SeqMetadata CSV file.
        clear_all: Whether to replace all existing importer-managed records.

    Raises:
        ValueError: If clearing was requested but no uploaded sequence is
            present in storage.

    """
    logger.info('Starting metadata upload.')
    blob_client = initialize_blob_client()
    seqids_in_cloud = get_seqids_in_cloud(blob_client)
    seqdata_dict = parse_seqtracking_csv(seqtracking_csv)
    update_seqdata_with_metadata(seqmetadata_csv, seqdata_dict)

    if clear_all and not set(seqdata_dict).intersection(seqids_in_cloud):
        raise ValueError(
            'Cannot clear existing metadata because no uploaded sequence '
            'matches a FASTA object in storage.'
        )

    with transaction.atomic():
        if clear_all:
            logger.warning('Clearing existing metadata before import.')
            clear_all_entries()

        existing_attributes = get_existing_attributes()
        bulk_operations = prepare_bulk_operations(
            seqdata_dict, seqids_in_cloud, existing_attributes
        )
        bulk_create_entries(bulk_operations)
        update_existing_entries(bulk_operations['sequence_data_to_update'])

    logger.info('Metadata upload completed successfully.')


class Command(BaseCommand):
    """Update metadata from SeqTracking and SeqMetadata CSV exports."""

    help = (
        'Updates metadata using a SeqTracking.csv and SeqMetadata.csv from '
        'the OLC Access database.'
    )

    def add_arguments(self, parser) -> None:
        """Register CSV input and clear-all command arguments.

        Args:
            parser: The command-line argument parser.

        """
        parser.add_argument(
            'seqtracking_csv',
            type=str,
            help='Path to SeqTracking.csv exported from OLC Access Database.',
        )
        parser.add_argument(
            'seqmetadata_csv',
            type=str,
            help='Path to SeqMetadata.csv exported from OLC Access Database.',
        )
        parser.add_argument(
            '-c',
            '--clear',
            action='store_true',
            help='Clear all existing entries in the database before upload.',
        )

    def handle(self, **options) -> None:
        """Run the metadata importer.

        Args:
            options: Parsed command-line options.

        Raises:
            CommandError: If the metadata import fails.

        """
        try:
            upload_metadata(
                seqtracking_csv=options['seqtracking_csv'],
                seqmetadata_csv=options['seqmetadata_csv'],
                clear_all=options['clear'],
            )
        except DuplicateEntryError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            logger.exception('Metadata upload failed.')
            raise CommandError(
                'Metadata upload failed; check the server log.'
            ) from exc
