"""Tests for Metadata Upload helpers and views."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from olc_webportalv2.metadata.models import OLNID, LabID, SequenceData

from .management.commands.upload_metadata import (
    Command,
    DataToUpload,
    DuplicateEntryError,
    get_seqids_in_cloud,
    parse_seqtracking_csv,
    upload_metadata,
)


class MetadataUploadStorageTests(unittest.TestCase):
    """Test storage-backed sequence ID discovery."""

    def test_get_seqids_in_cloud_lists_processed_fasta_objects(self):
        """Only FASTA object suffixes are interpreted as sequence IDs."""
        storage_client = Mock()
        storage_client.list_objects.return_value = [
            SimpleNamespace(name='sequence-1.fasta'),
            SimpleNamespace(name='folder/sequence-2.fasta'),
            SimpleNamespace(name='sequence-3.fasta.bak'),
            SimpleNamespace(name='metadata.csv'),
        ]

        self.assertEqual(
            get_seqids_in_cloud(storage_client),
            {'sequence-1', 'folder/sequence-2'},
        )
        storage_client.list_objects.assert_called_once_with('processed-data')

    def test_seqtracking_parser_rejects_duplicate_sequence_ids(self):
        """Duplicate input rows must not silently overwrite metadata."""
        csv_data = (
            'SEQID,CuratorFlag,LabID,Genus,Species,Serotype,OLNID\n'
            'sequence-1,PASS,LAB-1,Listeria,monocytogenes,1/2a,OLN-1\n'
            'sequence-1,FAIL,LAB-2,Escherichia,coli,O157,OLN-2\n'
        )

        with patch('builtins.open', mock_open(read_data=csv_data)):
            with self.assertRaises(DuplicateEntryError):
                parse_seqtracking_csv('tracking.csv')


class MetadataUploadImportTests(TestCase):
    """Test import updates and clear-all transaction behavior."""

    def setUp(self):
        """Create an existing sequence record to update or preserve."""
        self.sequence = SequenceData.objects.create(
            seqid='sequence-1',
            quality='Fail',
            genus='Old genus',
            species='old species',
            serotype='old serotype',
            mlst='1',
            rmlst='1',
        )

    def _upload_record(self):
        """Create populated metadata for the existing sequence."""
        seqdata = DataToUpload(self.sequence.seqid)
        seqdata.quality = 'Pass'
        seqdata.genus = 'Listeria'
        seqdata.species = 'monocytogenes'
        seqdata.serotype = '1/2a'
        seqdata.mlst = '2'
        seqdata.rmlst = '3'
        seqdata.labid = 'LAB-1'
        seqdata.olnid = 'OLN-1'
        return seqdata

    def _mock_import_inputs(self, seqdata):
        """Patch storage and parsing dependencies for an upload.

        Args:
            seqdata: Metadata record to return from the SeqTracking parser.

        """
        return (
            patch(
                'olc_webportalv2.metadata_upload.management.commands.'
                'upload_metadata.initialize_blob_client',
                return_value=Mock(),
            ),
            patch(
                'olc_webportalv2.metadata_upload.management.commands.'
                'upload_metadata.get_seqids_in_cloud',
                return_value={self.sequence.seqid},
            ),
            patch(
                'olc_webportalv2.metadata_upload.management.commands.'
                'upload_metadata.parse_seqtracking_csv',
                return_value={self.sequence.seqid: seqdata},
            ),
            patch(
                'olc_webportalv2.metadata_upload.management.commands.'
                'upload_metadata.update_seqdata_with_metadata',
            ),
        )

    def test_upload_updates_existing_sequence_metadata(self):
        """Existing sequence rows receive imported metadata and IDs."""
        patches = self._mock_import_inputs(self._upload_record())
        with patches[0], patches[1], patches[2], patches[3]:
            upload_metadata('tracking.csv', 'metadata.csv')
            upload_metadata('tracking.csv', 'metadata.csv')

        self.sequence.refresh_from_db()
        self.assertEqual(self.sequence.quality, 'Pass')
        self.assertEqual(self.sequence.genus, 'Listeria')
        self.assertEqual(self.sequence.species, 'monocytogenes')
        self.assertEqual(self.sequence.mlst, '2')
        self.assertEqual(self.sequence.labid.labid, 'LAB-1')
        self.assertEqual(self.sequence.olnid.olnid, 'OLN-1')
        self.assertEqual(LabID.objects.count(), 1)
        self.assertEqual(OLNID.objects.count(), 1)

    def test_clear_all_import_rolls_back_on_failure(self):
        """A failed replacement import preserves the preexisting database."""
        patches = self._mock_import_inputs(self._upload_record())
        with patches[0], patches[1], patches[2], patches[3]:
            with patch(
                'olc_webportalv2.metadata_upload.management.commands.'
                'upload_metadata.bulk_create_entries',
                side_effect=RuntimeError('database failure'),
            ):
                with self.assertRaises(RuntimeError):
                    upload_metadata(
                        'tracking.csv',
                        'metadata.csv',
                        clear_all=True,
                    )

        self.assertTrue(
            SequenceData.objects.filter(pk=self.sequence.pk).exists()
        )

    def test_command_reports_import_failures(self):
        """Management commands return a nonzero failure rather than swallow."""
        with patch(
            'olc_webportalv2.metadata_upload.management.commands.'
            'upload_metadata.upload_metadata',
            side_effect=ValueError('bad input'),
        ):
            with self.assertRaises(CommandError):
                Command().handle(
                    seqtracking_csv='tracking.csv',
                    seqmetadata_csv='metadata.csv',
                    clear=False,
                )


class MetadataUploadViewTests(TestCase):
    """Test temporary file handling at the upload endpoint."""

    @staticmethod
    def _uploaded_files():
        """Create validly named uploaded CSV fixtures."""
        return {
            'seqtracking_file': SimpleUploadedFile(
                'SeqTracking.csv',
                b'SEQID,CuratorFlag,LabID,Genus,Species,Serotype,OLNID\\n',
            ),
            'seqmetadata_file': SimpleUploadedFile(
                'SeqMetadata.csv',
                b'SeqID,MLST_Result,rMLST_Result\\n',
            ),
        }

    def test_upload_removes_temporary_files_after_success(self):
        """Successful uploads remove CSVs saved for processing."""
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                with patch(
                    'olc_webportalv2.metadata_upload.views.upload_metadata'
                ) as importer:
                    response = self.client.post(
                        reverse('metadata_upload:upload_files'),
                        self._uploaded_files(),
                    )

                self.assertRedirects(
                    response,
                    reverse('metadata_upload:upload_success'),
                )
                self.assertEqual(os.listdir(media_root), [])
                importer.assert_called_once()

    def test_upload_hides_import_errors_and_removes_temporary_files(self):
        """Failed uploads hide internal errors and still clean up files."""
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                with patch(
                    'olc_webportalv2.metadata_upload.views.upload_metadata',
                    side_effect=RuntimeError('database internals'),
                ):
                    response = self.client.post(
                        reverse('metadata_upload:upload_files'),
                        self._uploaded_files(),
                    )

                self.assertContains(response, 'Metadata upload failed.')
                self.assertNotContains(response, 'database internals')
                self.assertEqual(os.listdir(media_root), [])
