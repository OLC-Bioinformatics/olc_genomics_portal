"""Provider-neutral client for the internal Storage service."""

import base64
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
import requests

from django.conf import settings


@dataclass(frozen=True)
class StorageObject:
    name: str
    size: int | None
    last_modified: str | None
    etag: str | None
    content_md5: str | None
    content_type: str | None


@dataclass(frozen=True)
class StorageContainer:
    name: str
    created_at: str | None
    metadata: dict


class StorageServiceError(RuntimeError):
    """Raised when the Storage service returns an unsuccessful response."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class StorageClient:
    """HTTP client implementing the provider-neutral Storage contract."""

    def __init__(self, base_url=None, token=None, session=None, timeout=None):
        self.base_url = (base_url or settings.STORAGE_SERVICE_URL).rstrip("/")
        self.token = token or settings.STORAGE_SERVICE_TOKEN
        self.session = session or requests.Session()
        self.timeout = timeout or settings.STORAGE_REQUEST_TIMEOUT

    def list_containers(self):
        data = self._request("GET", "/v1/containers").json()
        return [
            StorageContainer(
                name=item["name"],
                created_at=item.get("created_at"),
                metadata=item.get("metadata") or {},
            )
            for item in data["items"]
        ]

    def ensure_container(self, container):
        self._request("PUT", f"/v1/containers/{quote(container, safe='')}")

    def delete_container(self, container):
        self._request("DELETE", f"/v1/containers/{quote(container, safe='')}")

    def rename_container(self, old_name, new_name):
        self._request(
            "POST",
            "/v1/containers/rename",
            json={"old_name": old_name, "new_name": new_name},
        )

    def list_objects(self, container, prefix=None):
        response = self._request(
            "GET",
            f"/v1/containers/{quote(container, safe='')}/objects",
            params={"prefix": prefix} if prefix else None,
        )
        return [self._object(item) for item in response.json()["items"]]

    def get_object(self, container, name):
        response = self._request(
            "HEAD",
            self._object_path(container, name),
        )
        return StorageObject(
            name=name,
            size=int(response.headers["Content-Length"]),
            last_modified=response.headers.get("Last-Modified"),
            etag=response.headers.get("ETag"),
            content_md5=response.headers.get("Content-MD5"),
            content_type=response.headers.get("Content-Type"),
        )

    def object_exists(self, container, name):
        try:
            self.get_object(container, name)
        except StorageServiceError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    def upload_bytes(
        self, container, name, data, content_type=None, content_md5=None
    ):
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        if content_md5:
            headers["Content-MD5"] = (
                base64.b64encode(content_md5).decode("ascii")
                if isinstance(content_md5, bytes)
                else content_md5
            )
        response = self._request(
            "PUT",
            self._object_path(container, name),
            data=data,
            headers=headers,
        )
        return self._object(response.json())

    def upload_file(
        self, container, name, file_path, content_type=None, content_md5=None
    ):
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        if content_md5:
            headers["Content-MD5"] = content_md5
        with open(file_path, "rb") as source:
            response = self._request(
                "PUT",
                self._object_path(container, name),
                data=source,
                headers=headers,
            )
        return self._object(response.json())

    def download_bytes(self, container, name):
        response = self._request(
            "GET",
            self._object_path(container, name),
        )
        return response.content

    def download_file(self, container, name, file_path):
        destination = Path(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self._request(
            "GET",
            self._object_path(container, name),
            stream=True,
        )
        try:
            with destination.open("wb") as target:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        target.write(chunk)
        finally:
            response.close()

    def delete_object(self, container, name):
        self._request("DELETE", self._object_path(container, name))

    def copy_object(self, source_container, source_name, destination_container, destination_name):
        response = self._request(
            "POST",
            "/v1/objects/copy",
            json={
                "source_container": source_container,
                "source_name": source_name,
                "destination_container": destination_container,
                "destination_name": destination_name,
            },
        )
        return self._object(response.json())

    def create_transfer_url(
        self,
        container,
        action,
        *,
        name=None,
        expires_in_seconds=3600,
        content_disposition=None,
    ):
        response = self._request(
            "POST",
            "/v1/transfer-urls",
            json={
                "container": container,
                "name": name,
                "action": action,
                "expires_in_seconds": expires_in_seconds,
                "content_disposition": content_disposition,
            },
        )
        return response.json()["url"]

    def _request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise StorageServiceError(None, f"Storage service request failed: {exc}") from exc
        if not response.ok:
            try:
                message = response.json().get("error", response.text)
            except ValueError:
                message = response.text
            raise StorageServiceError(response.status_code, message)
        return response

    @staticmethod
    def _object_path(container, name):
        return (
            f"/v1/containers/{quote(container, safe='')}/objects/"
            f"{quote(name, safe='/')}"
        )

    @staticmethod
    def _object(item):
        return StorageObject(
            name=item["name"],
            size=item.get("size"),
            last_modified=item.get("last_modified"),
            etag=item.get("etag"),
            content_md5=item.get("content_md5"),
            content_type=item.get("content_type"),
        )


def get_storage_client():
    return StorageClient()
