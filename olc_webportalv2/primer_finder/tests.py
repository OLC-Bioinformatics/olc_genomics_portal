from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from olc_webportalv2.primer_finder.methods import create_blob_from_summary
from olc_webportalv2.primer_finder.tasks import _generate_input_blob_sas_urls


class PrimerFinderStorageTests(unittest.TestCase):
    @patch("olc_webportalv2.primer_finder.tasks.get_storage_client")
    def test_input_urls_are_deduplicated_and_expiring(self, get_client):
        client = get_client.return_value
        client.object_exists.return_value = True
        client.create_transfer_url.side_effect = ["first-url", "second-url"]

        result = _generate_input_blob_sas_urls(
            container_name="primer-run",
            blob_names=["panel.json", "primers.csv", "panel.json"],
        )

        self.assertEqual(
            result,
            [
                {"blob_name": "panel.json", "sas_url": "first-url"},
                {"blob_name": "primers.csv", "sas_url": "second-url"},
            ],
        )
        self.assertEqual(
            client.object_exists.call_args_list,
            [
                call("primer-run", "panel.json"),
                call("primer-run", "primers.csv"),
            ],
        )
        self.assertEqual(
            client.create_transfer_url.call_args_list,
            [
                call(
                    "primer-run",
                    "download",
                    name="panel.json",
                    expires_in_seconds=12 * 60 * 60,
                ),
                call(
                    "primer-run",
                    "download",
                    name="primers.csv",
                    expires_in_seconds=12 * 60 * 60,
                ),
            ],
        )

    @patch("olc_webportalv2.primer_finder.tasks.get_storage_client")
    def test_input_url_generation_rejects_missing_blob(self, get_client):
        client = get_client.return_value
        client.object_exists.return_value = False

        with self.assertRaisesRegex(
            FileNotFoundError, "primer-run/missing.json"
        ):
            _generate_input_blob_sas_urls(
                container_name="primer-run",
                blob_names=["missing.json"],
            )

        client.create_transfer_url.assert_not_called()

    @patch("olc_webportalv2.primer_finder.methods.get_storage_client")
    def test_summary_is_uploaded_through_storage_client(self, get_client):
        request = SimpleNamespace(
            container_namer=Mock(return_value="primer-run"),
            summary='{"status": "complete"}',
        )

        create_blob_from_summary(request=request)

        get_client.return_value.upload_bytes.assert_called_once_with(
            "primer-run",
            "reports/primer-run_summary_report.json",
            b'{"status": "complete"}',
        )
