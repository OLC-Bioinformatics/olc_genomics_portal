"""Contract tests for the internal Storage HTTP API."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("AZURE_ACCOUNT_NAME", "storageunittest")
os.environ.setdefault("AZURE_ACCOUNT_KEY", "dGVzdA==")
os.environ.setdefault("SECRET_KEY", "storage-test-secret")

from api import create_app
from provider import AzureStorageProvider


class StorageApiTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.provider.list_containers.return_value = [
            {"name": "reads", "created_at": None, "metadata": {}}
        ]
        self.provider.list_objects.return_value = [
            {
                "name": "sample.fastq",
                "size": 4,
                "last_modified": None,
                "etag": "etag",
                "content_md5": None,
                "content_type": "application/octet-stream",
            }
        ]
        self.provider.get_object.return_value = self.provider.list_objects.return_value[0]
        self.provider.upload_object.return_value = self.provider.get_object.return_value
        self.provider.download_object.return_value = (
            SimpleNamespace(
                content_settings=SimpleNamespace(
                    content_type="application/octet-stream"
                )
            ),
            [b"data"],
        )
        self.provider.create_transfer_url.return_value = "https://transfer.example/url"
        self.app = create_app(provider=self.provider, token="test-token")
        self.client = self.app.test_client()
        self.headers = {"Authorization": "Bearer test-token"}

    def test_internal_routes_require_authentication(self):
        response = self.client.get("/v1/containers")
        self.assertEqual(response.status_code, 401)

    def test_list_container_and_objects_contract(self):
        response = self.client.get("/v1/containers/reads/objects", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["items"][0]["name"], "sample.fastq")
        self.assertEqual(response.json["items"][0]["size"], 4)
        self.provider.list_objects.assert_called_once_with("reads", None)

    def test_upload_and_download_transfer_data(self):
        upload = self.client.put(
            "/v1/containers/reads/objects/sample.fastq",
            data=b"data",
            headers={
                **self.headers,
                "Content-Type": "application/fastq",
                "Content-MD5": "b64-md5",
            },
        )
        self.assertEqual(upload.status_code, 201)
        args = self.provider.upload_object.call_args.kwargs
        self.assertEqual(args["container"], "reads")
        self.assertEqual(args["name"], "sample.fastq")
        self.assertEqual(args["data"].read(), b"data")
        self.assertEqual(args["content_type"], "application/fastq")
        self.assertEqual(args["content_md5"], "b64-md5")

        download = self.client.get(
            "/v1/containers/reads/objects/sample.fastq",
            headers=self.headers,
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"data")

    def test_transfer_url_contract(self):
        response = self.client.post(
            "/v1/transfer-urls",
            headers=self.headers,
            json={
                "container": "reads",
                "name": "sample.fastq",
                "action": "write",
                "expires_in_seconds": 600,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["url"], "https://transfer.example/url")
        self.provider.create_transfer_url.assert_called_once_with(
            container="reads",
            name="sample.fastq",
            action="write",
            expires_in_seconds=600,
            content_disposition=None,
        )

    def test_readwrite_transfer_url_contract(self):
        response = self.client.post(
            "/v1/transfer-urls",
            headers=self.headers,
            json={
                "container": "reads",
                "action": "readwrite",
                "expires_in_seconds": 604800,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.provider.create_transfer_url.assert_called_once_with(
            container="reads",
            name=None,
            action="readwrite",
            expires_in_seconds=604800,
            content_disposition=None,
        )

    def test_readwrite_sas_grants_container_read_and_write(self):
        provider = AzureStorageProvider.__new__(AzureStorageProvider)
        provider.account_name = "storageaccount"
        provider.account_key = "test-key"
        provider.client = Mock()
        provider.client.get_container_client.return_value.url = (
            "https://storageaccount.blob.core.windows.net/reads"
        )
        with patch(
            "provider.generate_container_sas",
            return_value="sas-token",
        ) as generate_sas:
            result = provider.create_transfer_url(
                container="reads",
                action="readwrite",
                expires_in_seconds=604800,
            )
        permissions = generate_sas.call_args.kwargs["permission"]
        self.assertTrue(permissions.read)
        self.assertTrue(permissions.list)
        self.assertTrue(permissions.write)
        self.assertTrue(permissions.create)
        self.assertTrue(permissions.delete)
        self.assertTrue(result.endswith("?sas-token"))

    def test_reject_invalid_names_and_expiry(self):
        invalid_name = self.client.get(
            "/v1/containers/reads/objects/../escape",
            headers=self.headers,
        )
        self.assertEqual(invalid_name.status_code, 400)

        invalid_expiry = self.client.post(
            "/v1/transfer-urls",
            headers=self.headers,
            json={
                "container": "reads",
                "name": "sample.fastq",
                "action": "read",
                "expires_in_seconds": 604801,
            },
        )
        self.assertEqual(invalid_expiry.status_code, 400)


if __name__ == "__main__":
    unittest.main()
