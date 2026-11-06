from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from olc_webportalv2.cowsnphr.tasks import (
    container_sanity,
    find_containers,
    upload_files_for_cowsnphr,
)


class COWSNPhRStorageTests(unittest.TestCase):
    @patch("olc_webportalv2.cowsnphr.tasks.get_storage_client")
    def test_find_containers_excludes_output_containers(self, get_client):
        client = get_client.return_value
        client.list_containers.return_value = [
            SimpleNamespace(name="123456-run"),
            SimpleNamespace(name="cowsnphr-run"),
            SimpleNamespace(name="123456-run-output"),
            SimpleNamespace(name="unrelated"),
        ]

        self.assertEqual(
            find_containers(),
            ["123456-run", "cowsnphr-run"],
        )

    @patch("olc_webportalv2.cowsnphr.tasks.get_storage_client")
    def test_container_sanity_checks_required_paths(self, get_client):
        client = get_client.return_value
        client.list_objects.return_value = [
            SimpleNamespace(name="fastq/sample.fastq.gz"),
            SimpleNamespace(name="ref/reference.fasta"),
            SimpleNamespace(name="ref/regions.bed"),
        ]

        self.assertEqual(
            container_sanity("cowsnphr-run"),
            (True, True, "ref/regions.bed"),
        )
        client.list_objects.assert_called_once_with("cowsnphr-run")

    @patch("olc_webportalv2.cowsnphr.tasks.get_storage_client")
    def test_upload_files_routes_fastq_to_fastq_folder(self, get_client):
        client = get_client.return_value
        uploaded_file = SimpleNamespace(
            name="sample.fastq.gz",
            content_type="application/gzip",
            read=Mock(return_value=b"reads"),
        )

        upload_files_for_cowsnphr(uploaded_file, "cowsnphr-run")

        client.ensure_container.assert_called_once_with("cowsnphr-run")
        client.upload_bytes.assert_called_once_with(
            "cowsnphr-run",
            "fastq/sample.fastq.gz",
            b"reads",
            content_type="application/gzip",
        )
