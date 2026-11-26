import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import TestCase as DjangoTestCase

from olc_webportalv2.common.media_retention import (
    MediaRoot,
    build_media_roots,
    clean_local_media,
)


class MediaRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.media_root = Path(self.temporary_directory.name)
        self.run_root = self.media_root / "run-123"
        self.run_root.mkdir()
        self.local_file = self.run_root / "result.fastq"
        self.local_file.write_bytes(b"verified result")
        old_timestamp = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).timestamp()
        os.utime(self.local_file, (old_timestamp, old_timestamp))
        self.roots = {
            "run-123": MediaRoot("run-123", "run-123"),
        }
        self.storage = Mock()
        self.storage.list_objects.return_value = []

    def test_dry_run_reports_missing_copy_without_upload_or_deletion(self):
        report = clean_local_media(
            dry_run=True,
            retention_days=30,
            media_root=self.media_root,
            roots=self.roots,
            storage_client=self.storage,
        )

        self.assertTrue(self.local_file.exists())
        self.storage.upload_file.assert_not_called()
        self.storage.list_objects.assert_not_called()
        self.storage.delete_object.assert_not_called()
        self.assertEqual(report.entries[0].action, "would_delete")
        self.assertEqual(report.deleted_files, 0)

    def test_apply_deletes_stale_local_files_without_touching_blob_storage(self):
        report = clean_local_media(
            dry_run=False,
            retention_days=30,
            media_root=self.media_root,
            roots=self.roots,
            storage_client=self.storage,
        )

        self.storage.list_objects.assert_not_called()
        self.storage.upload_file.assert_not_called()
        self.storage.delete_container.assert_not_called()
        self.assertFalse(self.local_file.exists())
        self.assertEqual(report.deleted_files, 1)
        self.assertEqual(report.entries[0].action, "deleted")

    def test_apply_deletes_local_file_even_if_blob_copy_would_conflict(self):
        self.storage.list_objects.return_value = [
            SimpleNamespace(
                name="result.fastq",
                size=self.local_file.stat().st_size + 1,
                content_md5=None,
            )
        ]

        report = clean_local_media(
            dry_run=False,
            retention_days=30,
            media_root=self.media_root,
            roots=self.roots,
            storage_client=self.storage,
        )

        self.assertFalse(self.local_file.exists())
        self.storage.list_objects.assert_not_called()
        self.storage.upload_file.assert_not_called()
        self.assertEqual(report.deleted_files, 1)
        self.assertEqual(report.entries[0].action, "deleted")

    def test_unmapped_and_excluded_roots_are_not_scanned(self):
        excluded_root = self.media_root / "sequences"
        excluded_root.mkdir()
        (excluded_root / "reference.fasta").write_bytes(b"reference")
        unmapped_root = self.media_root / "unmapped"
        unmapped_root.mkdir()
        (unmapped_root / "local.txt").write_bytes(b"unmapped")

        report = clean_local_media(
            dry_run=True,
            retention_days=30,
            media_root=self.media_root,
            roots=self.roots,
            storage_client=self.storage,
        )

        self.assertEqual(report.scanned_files, 1)
        self.assertEqual(report.excluded_roots, 1)
        self.assertEqual(report.unmapped_roots, 1)
        self.storage.list_objects.assert_not_called()


class MediaRootMappingTests(DjangoTestCase):
    def test_existing_blob_container_maps_a_matching_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            run_directory = Path(directory) / "run-123"
            run_directory.mkdir()

            roots = build_media_roots(
                existing_container_names={"run-123"},
                media_root=directory,
            )

        self.assertEqual(roots["run-123"], MediaRoot("run-123", "run-123"))
