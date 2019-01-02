from django.core.management.base import BaseCommand
from django.conf import settings
import re
import datetime
from datetime import timezone
from azure.storage.blob import BlockBlobService


def clean_old_containers():
    blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                   account_key=settings.AZURE_ACCOUNT_KEY)
    # Patterns we have to worry about - data-request-digits, geneseekr-digits
    # TODO: Add more of these as more analysis types get created.
    patterns_to_search = ['^data-request-\d+$', '^geneseekr-\d+$']
    generator = blob_client.list_containers(include_metadata=True)
    for container in generator:
        for pattern in patterns_to_search:
            if re.match(pattern, container.name):
                today = datetime.datetime.now(timezone.utc)
                container_age = abs(container.properties.last_modified - today).days
                if container_age > 7:
                    blob_client.delete_container(container.name)


class Command(BaseCommand):
    help = 'Command to clean out result containers for data/analysis requests that are >7 days old. ' \
           'Should be run every day or so.'

    def handle(self, *args, **options):
        clean_old_containers()
