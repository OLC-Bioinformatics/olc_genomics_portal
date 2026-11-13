"""Provider-neutral storage boundary for research-run workflows."""

from time import sleep

from olc_webportalv2.common.storage_client import (
    StorageServiceError,
    get_storage_client,
)


class ResearchStorage(object):
    """Provider contract used by research-run upload views."""

    def ensure_container(self, container_name):
        """Ensure that a named container exists.

        Args:
            container_name: Storage container to create if missing.

        """
        raise NotImplementedError

    def upload_url(self, container_name, blob_name, minutes=120):
        """Create a temporary URL for uploading an object.

        Args:
            container_name: Destination container.
            blob_name: Destination object path.
            minutes: URL lifetime in minutes.

        """
        raise NotImplementedError

    def download_blob(self, container_name, blob_name, file_path):
        """Download an object to a local path.

        Args:
            container_name: Source container.
            blob_name: Source object path.
            file_path: Local destination path.

        """
        raise NotImplementedError

    def list_files(self, container_name):
        """List object paths in a container.

        Args:
            container_name: Container to enumerate.

        """
        raise NotImplementedError

    def list_file_entries(self, container_name):
        """List object paths and sizes in a container.

        Args:
            container_name: Container to enumerate.

        """
        raise NotImplementedError

    def list_containers(self):
        """List available container names."""
        raise NotImplementedError

    def delete_file(self, container_name, blob_name):
        """Delete an object from a container.

        Args:
            container_name: Container holding the object.
            blob_name: Object path to delete.

        """
        raise NotImplementedError

    def delete_container(self, container_name):
        """Delete a storage container.

        Args:
            container_name: Container to delete.

        """
        raise NotImplementedError

    def rename_container(self, old_name, new_name):
        """Rename a storage container.

        Args:
            old_name: Current container name.
            new_name: Replacement container name.

        """
        raise NotImplementedError


class ServiceResearchStorage(ResearchStorage):
    """Research storage adapter backed by the internal Storage service."""

    def __init__(self):
        """Create an adapter using the configured Storage service client."""
        self.client = get_storage_client()

    def ensure_container(self, container_name):
        """Ensure that the named container exists.

        Args:
            container_name: Storage container to create if missing.

        """
        self.client.ensure_container(container_name)

    def upload_url(self, container_name, blob_name, minutes=30):
        """Create a temporary URL for uploading an object.

        Args:
            container_name: Destination container.
            blob_name: Destination object path.
            minutes: URL lifetime in minutes.

        """
        return self.client.create_transfer_url(
            container=container_name,
            name=blob_name,
            action="write",
            expires_in_seconds=minutes * 60,
        )

    def download_blob(self, container_name, blob_name, file_path):
        """Download an object with retries for transient service errors.

        Args:
            container_name: Source container.
            blob_name: Source object path.
            file_path: Local destination path.

        """
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
                sleep(2**attempt)

    def list_files(self, container_name):
        """List object paths in a container.

        Args:
            container_name: Container to enumerate.

        """
        return [item.name for item in self.client.list_objects(container_name)]

    def list_file_entries(self, container_name):
        """List object paths and sizes in a container.

        Args:
            container_name: Container to enumerate.

        """
        return [
            {"name": item.name, "size": item.size}
            for item in self.client.list_objects(container_name)
        ]

    def list_containers(self):
        """List available container names."""
        return [container.name for container in self.client.list_containers()]

    def delete_file(self, container_name, blob_name):
        """Delete an object from a container.

        Args:
            container_name: Container holding the object.
            blob_name: Object path to delete.

        """
        return self.client.delete_object(container_name, blob_name)

    def delete_container(self, container_name):
        """Delete a storage container.

        Args:
            container_name: Container to delete.

        """
        return self.client.delete_container(container_name)

    def rename_container(self, old_name, new_name):
        """Rename a storage container.

        Args:
            old_name: Current container name.
            new_name: Replacement container name.

        """
        self.client.rename_container(old_name, new_name)


def get_research_storage():
    """Return the configured research storage provider."""
    return ServiceResearchStorage()
