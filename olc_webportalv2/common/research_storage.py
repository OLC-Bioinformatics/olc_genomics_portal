"""Storage boundary for the research-run upload workflow."""

from datetime import datetime, timedelta
from time import sleep

from azure.common import AzureConflictHttpError
from azure.storage.blob import BlockBlobService, BlobPermissions
from django.conf import settings


class ResearchStorage(object):
    """Provider contract used by research-run upload views."""

    def ensure_container(self, container_name):
        raise NotImplementedError

    def upload_url(self, container_name, blob_name, minutes=120):
        raise NotImplementedError

    def download_blob(self, container_name, blob_name, file_path):
        raise NotImplementedError

    def list_files(self, container_name):
        raise NotImplementedError

    def list_file_entries(self, container_name):
        raise NotImplementedError

    def list_containers(self):
        raise NotImplementedError

    def delete_file(self, container_name, blob_name):
        raise NotImplementedError

    def delete_container(self, container_name):
        raise NotImplementedError

    def rename_container(self, old_name, new_name):
        raise NotImplementedError


class AzureResearchStorage(ResearchStorage):
    """Azure Blob implementation of the research storage contract."""

    def __init__(self):
        self.client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY,
        )

    def ensure_container(self, container_name):
        try:
            self.client.create_container(container_name)
        except AzureConflictHttpError:
            pass

    def upload_url(self, container_name, blob_name, minutes=30):
        sas_token = self.client.generate_blob_shared_access_signature(
            container_name=container_name,
            blob_name=blob_name,
            permission=BlobPermissions(create=True, write=True),
            expiry=datetime.utcnow() + timedelta(minutes=minutes),
        )
        return self.client.make_blob_url(
            container_name=container_name,
            blob_name=blob_name,
            sas_token=sas_token,
        )

    def download_blob(self, container_name, blob_name, file_path):
        for attempt in range(3):
            try:
                return self.client.get_blob_to_path(
                    container_name=container_name,
                    blob_name=blob_name,
                    file_path=file_path,
                )
            except Exception:
                if attempt == 2:
                    raise
                sleep(2 ** attempt)

    def list_files(self, container_name):
        return [blob.name for blob in self.client.list_blobs(container_name)]

    def list_file_entries(self, container_name):
        entries = []
        for blob in self.client.list_blobs(container_name):
            properties = getattr(blob, 'properties', blob)
            entries.append({
                'name': blob.name,
                'size': getattr(properties, 'content_length', None),
            })
        return entries

    def list_containers(self):
        return [container.name for container in self.client.list_containers()]

    def delete_file(self, container_name, blob_name):
        return self.client.delete_blob(container_name, blob_name)

    def delete_container(self, container_name):
        return self.client.delete_container(container_name)

    def rename_container(self, old_name, new_name):
        if old_name == new_name:
            return
        if new_name in self.list_containers():
            raise ValueError('The destination container already exists.')
        self.ensure_container(new_name)
        blob_names = self.list_files(old_name)
        try:
            for blob_name in blob_names:
                sas_token = self.client.generate_blob_shared_access_signature(
                    container_name=old_name,
                    blob_name=blob_name,
                    permission=BlobPermissions(read=True),
                    expiry=datetime.utcnow() + timedelta(hours=1),
                )
                source_url = self.client.make_blob_url(
                    container_name=old_name,
                    blob_name=blob_name,
                    sas_token=sas_token,
                )
                self.client.copy_blob(new_name, blob_name, source_url)
                for attempt in range(300):
                    blob = self.client.get_blob_properties(new_name, blob_name)
                    blob_properties = getattr(blob, 'properties', blob)
                    copy_properties = getattr(blob_properties, 'copy', None)
                    if isinstance(copy_properties, dict):
                        copy_status = copy_properties.get('status')
                    else:
                        copy_status = getattr(copy_properties, 'status', None)
                    if copy_status == 'success':
                        break
                    if copy_status == 'failed' or attempt == 299:
                        raise RuntimeError('Blob copy failed for {0}.'.format(blob_name))
                    sleep(1)
        except Exception:
            for blob_name in self.list_files(new_name):
                self.delete_file(new_name, blob_name)
            self.delete_container(new_name)
            raise
        for blob_name in blob_names:
            self.delete_file(old_name, blob_name)
        self.client.delete_container(old_name)


def get_research_storage():
    """Return the configured research storage provider."""
    return AzureResearchStorage()