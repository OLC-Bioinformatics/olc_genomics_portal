import unittest
from unittest.mock import call, patch

from olc_webportalv2.filezone.methods import copy_blobs, generate_sas


class FileZoneStorageTests(unittest.TestCase):
    @patch("olc_webportalv2.filezone.methods.get_storage_client")
    def test_generate_sas_uses_attachment_transfer_url(self, get_client):
        client = get_client.return_value
        client.create_transfer_url.return_value = "download-url"

        result = generate_sas("folder/result.zip", "run-1")

        self.assertEqual(result, "download-url")
        client.create_transfer_url.assert_called_once_with(
            "run-1",
            "download",
            name="folder/result.zip",
            expires_in_seconds=7 * 24 * 60 * 60,
            content_disposition='attachment; filename="result.zip"',
        )

    @patch("olc_webportalv2.filezone.methods.get_storage_client")
    def test_copy_blobs_uses_storage_copy_contract(self, get_client):
        client = get_client.return_value

        copy_blobs(
            [
                {
                    "container_name": "source-1",
                    "blob_name": "reports/result.zip",
                },
                {
                    "container_name": "source-2",
                    "blob_name": "data/sequence.fasta",
                },
            ],
            "destination",
        )

        client.ensure_container.assert_called_once_with("destination")
        self.assertEqual(
            client.copy_object.call_args_list,
            [
                call(
                    "source-1",
                    "reports/result.zip",
                    "destination",
                    "reports/result.zip",
                ),
                call(
                    "source-2",
                    "data/sequence.fasta",
                    "destination",
                    "data/sequence.fasta",
                ),
            ],
        )
