from django.core.management.base import BaseCommand
from olc_webportalv2.metadata.models import SequenceData
from django.conf import settings
import csv

from azure.storage.blob import BlockBlobService


def upload_metadata(metadata_csv):
    blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                   account_key=settings.AZURE_ACCOUNT_KEY)
    seqids_in_cloud = list()
    generator = blob_client.list_blobs('processed-data')
    for blob in generator:
        seqids_in_cloud.append(blob.name.replace('.fasta', ''))

    acceptable_qualities = ('Pass', 'Fail', 'Reference')
    with open(metadata_csv) as csvFile:
        reader = csv.DictReader(csvFile)
        for row in reader:
            if row['SeqID'] not in seqids_in_cloud:
                print('WARNING: SeqID {} was listed on metadata sheet, but is not stored in cloud.'.format(row['SeqID']))
                continue
            if row['Quality'] not in acceptable_qualities:
                raise AttributeError('Quality for SeqID {} was listed as {}. Only acceptable values are Pass, Fail, and'
                                     ' Reference.'.format(row['SeqID'], row['Quality']))
            # Check we don't already have the listed SEQID in database. If we do, update..
            if not SequenceData.objects.filter(seqid=row['SeqID']).exists():
                SequenceData.objects.create(seqid=row['SeqID'],
                                            quality=row['Quality'],
                                            genus=row['Genus'])
            else:
                sequence_data = SequenceData.objects.get(seqid=row['SeqID'])
                sequence_data.quality = row['Quality']
                sequence_data.genus = row['Genus']
                sequence_data.save()


class Command(BaseCommand):
    help = 'Command to upload metadata into our metadata table. ' \
           'Input file should be a CSV with columns SEQID, Genus, and Quality, where quality is Fail, Pass, or ' \
           'Reference. All SEQIDs must have a corresponding assembly in a container called processed-data in Azure ' \
           'Storage Container'

    def add_arguments(self, parser):
        parser.add_argument('metadata_csv',
                            type=str,
                            help='Full path to metadata sheet.')

    def handle(self, *args, **options):
        upload_metadata(metadata_csv=options['metadata_csv'])
