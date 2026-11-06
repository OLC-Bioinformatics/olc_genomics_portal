"""Provider-neutral storage boundary for research-run workflows."""

from time import sleep

from olc_webportalv2.common.storage_client import (
    StorageServiceError,
    get_storage_client,
)


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


class ServiceResearchStorage(ResearchStorage):
    """Research storage adapter backed by the internal Storage service."""

    def __init__(self):
        self.client = get_storage_client()

    def ensure_container(self, container_name):
        self.client.ensure_container(container_name)

    def upload_url(self, container_name, blob_name, minutes=30):
        return self.client.create_transfer_url(
            container=container_name,
            name=blob_name,
            action="write",
            expires_in_seconds=minutes * 60,
        )

    def download_blob(self, container_name, blob_name, file_path):
        for attempt in range(3):
            try:
                return self.client.download_file(
                    container=container_name,
                    name=blob_name,
                    file_path=file_path,
                )
            except StorageServiceError as exc:
                if exc.status_code is not None and exc.status_code < 500:
                    raise
                if attempt == 2:
                    raise
                sleep(2 ** attempt)

    def list_files(self, container_name):
        return [
            item.name for item in self.client.list_objects(container_name)
        ]

    def list_file_entries(self, container_name):
        return [
            {"name": item.name, "size": item.size}
            for item in self.client.list_objects(container_name)
        ]

    def list_containers(self):
        return [container.name for container in self.client.list_containers()]

    def delete_file(self, container_name, blob_name):
        return self.client.delete_object(container_name, blob_name)

    def delete_container(self, container_name):
        return self.client.delete_container(container_name)

    def rename_container(self, old_name, new_name):
        self.client.rename_container(old_name, new_name)


def get_research_storage():
    """Return the configured research storage provider."""
    return ServiceResearchStorage()