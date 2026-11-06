"""Azure implementation of the provider-neutral storage contract."""

import base64
from datetime import datetime, timedelta, timezone
from time import sleep

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.core.exceptions import AzureError
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContainerSasPermissions,
    ContentSettings,
    generate_blob_sas,
    generate_container_sas,
)


class StorageNotFoundError(Exception):
    """Raised when a requested container or object does not exist."""


class AzureStorageProvider:
    """Object storage provider backed by Azure Blob Storage."""

    def __init__(self, account_name, account_key):
        if not account_name or not account_key:
            raise RuntimeError("Azure storage credentials are not configured.")
        self.account_name = account_name
        self.account_key = account_key
        self.client = BlobServiceClient(
            account_url=f"https://{account_name}.blob.core.windows.net",
            credential=account_key,
        )

    def list_containers(self):
        return [
            {
                "name": container.name,
                "created_at": (
                    container.last_modified.isoformat()
                    if getattr(container, "last_modified", None)
                    else None
                ),
                "metadata": getattr(container, "metadata", None) or {},
            }
            for container in self.client.list_containers()
        ]

    def ensure_container(self, name):
        try:
            self.client.create_container(name)
        except ResourceExistsError:
            return

    def delete_container(self, name):
        try:
            self.client.delete_container(name)
        except ResourceNotFoundError as exc:
            raise StorageNotFoundError(name) from exc

    def list_objects(self, container, prefix=None):
        container_client = self.client.get_container_client(container)
        try:
            return [
                self._object_record(blob)
                for blob in container_client.list_blobs(name_starts_with=prefix)
            ]
        except ResourceNotFoundError as exc:
            raise StorageNotFoundError(container) from exc

    def get_object(self, container, name):
        blob_client = self.client.get_blob_client(container=container, blob=name)
        try:
            return self._object_record(blob_client.get_blob_properties())
        except ResourceNotFoundError as exc:
            raise StorageNotFoundError(f"{container}/{name}") from exc

    def upload_object(
        self, container, name, data, content_type=None, content_md5=None
    ):
        self.ensure_container(container)
        md5_bytes = base64.b64decode(content_md5) if content_md5 else None
        content_settings = ContentSettings(
            content_type=content_type,
            content_md5=md5_bytes,
        )
        blob_client = self.client.get_blob_client(container=container, blob=name)
        blob_client.upload_blob(
            data,
            overwrite=True,
            content_settings=content_settings,
        )
        return self.get_object(container, name)

    def download_object(self, container, name):
        blob_client = self.client.get_blob_client(container=container, blob=name)
        try:
            downloader = blob_client.download_blob()
            properties = downloader.properties
        except ResourceNotFoundError as exc:
            raise StorageNotFoundError(f"{container}/{name}") from exc
        return properties, downloader.chunks()

    def delete_object(self, container, name):
        blob_client = self.client.get_blob_client(container=container, blob=name)
        try:
            blob_client.delete_blob()
        except ResourceNotFoundError as exc:
            raise StorageNotFoundError(f"{container}/{name}") from exc

    def copy_object(
        self, source_container, source_name, destination_container, destination_name
    ):
        source_client = self.client.get_blob_client(
            container=source_container,
            blob=source_name,
        )
        destination_client = self.client.get_blob_client(
            container=destination_container,
            blob=destination_name,
        )
        try:
            source_client.get_blob_properties()
        except ResourceNotFoundError as exc:
            raise StorageNotFoundError(
                f"{source_container}/{source_name}"
            ) from exc
        self.ensure_container(destination_container)
        sas = generate_blob_sas(
            account_name=self.account_name,
            container_name=source_container,
            blob_name=source_name,
            account_key=self.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        source_url = f"{source_client.url}?{sas}"
        result = destination_client.start_copy_from_url(source_url)
        copy_id = result.get("copy_id")
        for _ in range(300):
            properties = destination_client.get_blob_properties()
            copy = properties.copy
            if copy.status == "success":
                return self.get_object(destination_container, destination_name)
            if copy.status == "failed":
                raise RuntimeError(
                    f"Copy failed for {source_container}/{source_name}."
                )
            if copy_id and copy.copy_id != copy_id:
                raise RuntimeError("Storage provider returned an unexpected copy ID.")
            sleep(1)
        raise TimeoutError(
            f"Copy timed out for {source_container}/{source_name}."
        )

    def rename_container(self, old_name, new_name):
        if old_name == new_name:
            return
        if any(item["name"] == new_name for item in self.list_containers()):
            raise ValueError("The destination container already exists.")
        objects = self.list_objects(old_name)
        self.ensure_container(new_name)
        try:
            for item in objects:
                self.copy_object(old_name, item["name"], new_name, item["name"])
        except (
            AzureError,
            RuntimeError,
            StorageNotFoundError,
            TimeoutError,
            ValueError,
        ):
            for item in self.list_objects(new_name):
                self.delete_object(new_name, item["name"])
            self.delete_container(new_name)
            raise
        for item in objects:
            self.delete_object(old_name, item["name"])
        self.delete_container(old_name)

    def create_transfer_url(
        self,
        container,
        action,
        expires_in_seconds,
        name=None,
        content_disposition=None,
    ):
        expiry = datetime.now(timezone.utc) + timedelta(
            seconds=expires_in_seconds
        )
        if name is None:
            permissions = ContainerSasPermissions(
                read=action in {"read", "readwrite"},
                list=action in {"read", "readwrite"},
                write=action in {"write", "readwrite"},
                create=action in {"write", "readwrite"},
                delete=action == "readwrite",
            )
            sas = generate_container_sas(
                account_name=self.account_name,
                container_name=container,
                account_key=self.account_key,
                permission=permissions,
                expiry=expiry,
            )
            container_url = self.client.get_container_client(container).url
            return f"{container_url}?{sas}"

        permissions = BlobSasPermissions(
            read=action in {"read", "readwrite"},
            write=action in {"write", "readwrite"},
            create=action in {"write", "readwrite"},
            delete=action == "readwrite",
        )
        sas = generate_blob_sas(
            account_name=self.account_name,
            container_name=container,
            blob_name=name,
            account_key=self.account_key,
            permission=permissions,
            expiry=expiry,
            content_disposition=content_disposition,
        )
        blob_url = self.client.get_blob_client(
            container=container,
            blob=name,
        ).url
        return f"{blob_url}?{sas}"

    @staticmethod
    def _object_record(blob):
        content_settings = getattr(blob, "content_settings", None)
        md5 = getattr(content_settings, "content_md5", None)
        return {
            "name": blob.name,
            "size": getattr(blob, "size", None),
            "last_modified": (
                blob.last_modified.isoformat()
                if getattr(blob, "last_modified", None)
                else None
            ),
            "etag": getattr(blob, "etag", None),
            "content_md5": base64.b64encode(md5).decode("ascii") if md5 else None,
            "content_type": getattr(content_settings, "content_type", None),
        }
