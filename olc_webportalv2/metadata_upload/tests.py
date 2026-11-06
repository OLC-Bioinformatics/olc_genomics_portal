from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from olc_webportalv2.metadata_upload.management.commands.upload_metadata import (
    get_seqids_in_cloud,
)


class MetadataUploadStorageTests(unittest.TestCase):
    def test_get_seqids_in_cloud_lists_processed_fasta_objects(self):
        storage_client = Mock()
        storage_client.list_objects.return_value = [
            SimpleNamespace(name="sequence-1.fasta"),
            SimpleNamespace(name="sequence-2.fasta"),
        ]

        self.assertEqual(
            get_seqids_in_cloud(storage_client),
            {"sequence-1", "sequence-2"},
        )
        storage_client.list_objects.assert_called_once_with("processed-data")
