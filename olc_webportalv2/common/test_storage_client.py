"""Tests for the provider-neutral Storage service client."""

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from olc_webportalv2.common.storage_client import (
    StorageClient,
    StorageServiceError,
)


class StorageClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.response = Mock()
        self.response.ok = True
        self.session.request.return_value = self.response
        self.client = StorageClient(
            base_url="http://storage:5000",
            token="internal-token",
            session=self.session,
            timeout=10,
        )

    def test_list_containers_maps_generic_response(self):
        self.response.json.return_value = {
            "items": [
                {
                    "name": "run-1",
                    "created_at": "2025-01-01T00:00:00Z",
                    "metadata": {"source": "upload"},
                }
            ]
        }

        containers = self.client.list_containers()

        self.assertEqual(containers[0].name, "run-1")
        self.assertEqual(containers[0].metadata, {"source": "upload"})
        self.assertEqual(
            self.session.request.call_args.args[:2],
            ("GET", "http://storage:5000/v1/containers"),
        )

    def test_list_objects_encodes_container_and_passes_prefix(self):
        self.response.json.return_value = {
            "items": [
                {
                    "name": "fastq/read-1.fastq.gz",
                    "size": 42,
                    "content_type": "application/gzip",
                }
            ]
        }

        objects = self.client.list_objects("run/1", prefix="fastq/")

        self.assertEqual(objects[0].name, "fastq/read-1.fastq.gz")
        self.assertEqual(objects[0].size, 42)
        self.assertEqual(objects[0].content_type, "application/gzip")
        call = self.session.request.call_args
        self.assertEqual(
            call.args[1],
            "http://storage:5000/v1/containers/run%2F1/objects",
        )
        self.assertEqual(call.kwargs["params"], {"prefix": "fastq/"})

    def test_upload_bytes_preserves_content_and_metadata(self):
        self.response.json.return_value = {
            "name": "report.json",
            "size": 3,
        }

        result = self.client.upload_bytes(
            "run-1",
            "report.json",
            b"{} ",
            content_type="application/json",
            content_md5=b"\x00\x01",
        )

        call = self.session.request.call_args
        self.assertEqual(call.args[0], "PUT")
        self.assertEqual(call.kwargs["data"], b"{} ")
        self.assertEqual(
            call.kwargs["headers"]["Content-Type"], "application/json"
        )
        self.assertEqual(
            call.kwargs["headers"]["Content-MD5"],
            base64.b64encode(b"\x00\x01").decode("ascii"),
        )
        self.assertEqual(result.name, "report.json")

    def test_object_exists_only_treats_not_found_as_false(self):
        self.response.ok = False
        self.response.status_code = 404
        self.response.json.return_value = {"error": "not found"}

        self.assertFalse(self.client.object_exists("run-1", "missing.fasta"))

        self.response.status_code = 503
        with self.assertRaises(StorageServiceError) as error:
            self.client.object_exists("run-1", "sequence.fasta")
        self.assertEqual(error.exception.status_code, 503)

    def test_download_file_streams_and_closes_response(self):
        self.response.iter_content.return_value = [b"", b"first", b"second"]

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "nested" / "result.txt"
            self.client.download_file("run-1", "result.txt", destination)

            self.assertEqual(destination.read_bytes(), b"firstsecond")

        self.response.close.assert_called_once_with()
        self.assertTrue(self.session.request.call_args.kwargs["stream"])

    def test_create_transfer_url_sends_provider_neutral_contract(self):
        self.response.json.return_value = {"url": "http://download/result"}

        url = self.client.create_transfer_url(
            "run-1",
            "download",
            name="reports/result.html",
            expires_in_seconds=3600,
            content_disposition='attachment; filename="result.html"',
        )

        self.assertEqual(url, "http://download/result")
        call = self.session.request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertEqual(
            call.kwargs["json"],
            {
                "container": "run-1",
                "name": "reports/result.html",
                "action": "download",
                "expires_in_seconds": 3600,
                "content_disposition": 'attachment; filename="result.html"',
            },
        )


if __name__ == "__main__":
    unittest.main()
