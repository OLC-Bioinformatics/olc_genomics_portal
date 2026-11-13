"""Tests for FileZone forms, storage helpers, archives, and tags."""

import base64
import hashlib
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, call, patch
from zipfile import ZipFile

from django.test import RequestFactory, SimpleTestCase

from olc_webportalv2.common.storage_client import StorageServiceError
from olc_webportalv2.filezone.forms import (
    ContainerCreateForm,
    FileLocateForm,
    validate_container_name,
)
from olc_webportalv2.filezone.methods import (
    FileLocate,
    calculate_checksum,
    copy_blobs,
    generate_sas,
)
from olc_webportalv2.filezone.tasks import (
    archive_multiple_containers,
    locate_files,
)
from olc_webportalv2.filezone.templatetags.filezone_tags import (
    render_blob_hierarchy,
)
from olc_webportalv2.filezone.views import container_view


class FileZoneStorageTests(SimpleTestCase):
    """Verify FileZone storage-client interactions."""

    @patch('olc_webportalv2.filezone.methods.get_storage_client')
    def test_generate_sas_uses_attachment_transfer_url(self, get_client):
        """Create a week-long download link with an attachment filename."""
        client = get_client.return_value
        client.create_transfer_url.return_value = 'download-url'

        result = generate_sas('folder/result.zip', 'run-1')

        self.assertEqual(result, 'download-url')
        client.create_transfer_url.assert_called_once_with(
            'run-1',
            'download',
            name='folder/result.zip',
            expires_in_seconds=7 * 24 * 60 * 60,
            content_disposition='attachment; filename="result.zip"',
        )

    @patch('olc_webportalv2.filezone.methods.get_storage_client')
    def test_copy_blobs_uses_storage_copy_contract(self, get_client):
        """Create the destination and preserve each source object path."""
        client = get_client.return_value

        copy_blobs(
            [
                {
                    'container_name': 'source-1',
                    'blob_name': 'reports/result.zip',
                },
                {
                    'container_name': 'source-2',
                    'blob_name': 'data/sequence.fasta',
                },
            ],
            'destination',
        )

        client.ensure_container.assert_called_once_with('destination')
        self.assertEqual(
            client.copy_object.call_args_list,
            [
                call(
                    'source-1',
                    'reports/result.zip',
                    'destination',
                    'reports/result.zip',
                ),
                call(
                    'source-2',
                    'data/sequence.fasta',
                    'destination',
                    'data/sequence.fasta',
                ),
            ],
        )

    def test_checksum_streams_file_and_resets_its_position(self):
        """Calculate an upload checksum without retaining the file contents."""
        file_obj = BytesIO(b'file contents')

        result = calculate_checksum(file_obj)

        expected = base64.b64encode(
            hashlib.md5(b'file contents').digest()
        ).decode()
        self.assertEqual(result, expected)
        self.assertEqual(file_obj.tell(), 0)

    def test_blob_checksum_downloads_to_a_temporary_file(self):
        """Calculate a blob checksum using streamed storage downloads."""
        storage = Mock()
        storage.download_file.side_effect = lambda container, name, path: Path(
            path
        ).write_bytes(b'large blob')
        blob = Mock()
        blob.name = 'result.fasta'

        result = FileLocate.calculate_md5(storage, blob, 'run-1')

        self.assertEqual(
            result,
            hashlib.md5(b'large blob').hexdigest(),
        )
        storage.download_file.assert_called_once()


class FileZoneFormTests(SimpleTestCase):
    """Cover invalid and boundary inputs in FileZone forms."""

    def test_missing_container_name_reports_form_error(self):
        """Keep a missing required field as a form error."""
        form = ContainerCreateForm(data={})

        self.assertFalse(form.is_valid())
        self.assertIn('container_name', form.errors)

    def test_container_name_with_no_valid_characters_returns_errors(self):
        """Reject punctuation-only names without raising SystemExit."""
        sanitized_name, errors = validate_container_name('!!!')

        self.assertEqual(sanitized_name, '')
        self.assertTrue(errors)

    def test_overlong_container_name_suggests_63_characters(self):
        """Suggest the maximum supported Azure container-name length."""
        sanitized_name, errors = validate_container_name('a' * 64)

        self.assertEqual(len(sanitized_name), 63)
        self.assertTrue(errors)

    @patch('olc_webportalv2.filezone.forms.find_containers', return_value=[])
    def test_valid_container_name_is_checked_for_uniqueness(
        self,
        find_containers,
    ):
        """Check storage uniqueness before accepting a valid name."""
        form = ContainerCreateForm(data={'container_name': 'my-container'})

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(
            form.cleaned_data['container_name'],
            'my-container',
        )
        find_containers.assert_called_once_with()

    def test_container_name_suggests_normalized_alternative(self):
        """Suggest lowercase names with underscores converted to dashes."""
        sanitized_name, errors = validate_container_name('My_container')

        self.assertEqual(sanitized_name, 'my-container')
        self.assertTrue(errors)

    @patch(
        'olc_webportalv2.filezone.forms.find_containers',
        side_effect=StorageServiceError(None, 'offline'),
    )
    def test_storage_failure_is_reported_as_form_error(self, find_containers):
        """Report storage outages while checking container uniqueness."""
        form = ContainerCreateForm(data={'container_name': 'valid-container'})

        self.assertFalse(form.is_valid())
        self.assertIn('container_name', form.errors)
        self.assertIn('Unable to verify', form.errors['container_name'][0])
        find_containers.assert_called_once_with()

    def test_missing_file_pattern_reports_field_error(self):
        """Require a file search pattern without indexing missing data."""
        form = FileLocateForm(data={})

        self.assertFalse(form.is_valid())
        self.assertIn('file_regex', form.errors)


class FileZoneTemplateTagTests(SimpleTestCase):
    """Verify storage-derived values are escaped in generated table rows."""

    def test_blob_names_are_escaped_before_rendering(self):
        """Treat blob names as text rather than executable markup."""
        blob_name = '<script>alert(1)</script>.fasta'
        rendered = render_blob_hierarchy(
            {
                blob_name: {
                    'pk': 1,
                    'blob_name': blob_name,
                    'blob_size': '1 KB',
                    'blob_date': '2026/01/01',
                    'blob_md5': 'checksum',
                    'blob_download_link': 'https://storage.example/download',
                }
            }
        )

        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)


class FileZoneArchiveTests(SimpleTestCase):
    """Verify selected blobs retain unique and safe archive paths."""

    @patch('olc_webportalv2.filezone.tasks.generate_archive_download_link')
    def test_archive_preserves_paths_across_containers(self, generate_link):
        """Avoid overwriting same-named files from different containers."""
        storage = Mock()
        storage.download_file.side_effect = lambda container, name, path: Path(
            path
        ).write_bytes(f'{container}/{name}'.encode())
        archived_names = []

        def capture_archive(
            blob_service_client,
            container_name,
            output_zipfile,
        ):
            with ZipFile(output_zipfile) as archive_file:
                archived_names.extend(archive_file.namelist())
            return 'download-url'

        generate_link.side_effect = capture_archive
        result = archive_multiple_containers(
            blob_client=storage,
            blob_list=[
                {
                    'container_name': 'first',
                    'blob_name': 'runs/result.txt',
                },
                {
                    'container_name': 'second',
                    'blob_name': 'runs/result.txt',
                },
                {
                    'container_name': 'third',
                    'blob_name': '../../etc/passwd',
                },
            ],
            filezone_pk=7,
        )

        self.assertEqual(result, 'download-url')
        self.assertEqual(
            archived_names,
            [
                'first/runs/result.txt',
                'second/runs/result.txt',
                'third/etc/passwd',
            ],
        )


class FileZoneTaskTests(SimpleTestCase):
    """Verify task behavior for incomplete search requests."""

    @patch('olc_webportalv2.filezone.tasks.Regexes.objects.get')
    def test_locate_files_rejects_missing_file_patterns(self, get_request):
        """Fail a malformed search instead of matching every stored blob."""
        request = Mock(file_regex_list=None)
        get_request.return_value = request

        with self.assertRaisesRegex(ValueError, 'no file search patterns'):
            locate_files.run(filezone_pk=17)

        self.assertEqual(request.status, 'Failed')
        request.save.assert_called_once_with(update_fields=['status'])


class FileZoneViewTests(SimpleTestCase):
    """Verify storage requests made by FileZone views."""

    @patch('olc_webportalv2.filezone.views.render')
    @patch('olc_webportalv2.filezone.views.prep_delete_blob')
    @patch('olc_webportalv2.filezone.views.get_storage_client')
    @patch('olc_webportalv2.filezone.views.list_blobs')
    @patch('olc_webportalv2.filezone.views.get_object_or_404')
    def test_delete_uses_the_active_storage_client(
        self,
        get_container,
        list_container_blobs,
        get_storage,
        delete_blobs,
        render_template,
    ):
        """Pass the initialized storage client to the delete helper."""
        storage = Mock()
        container = Mock(pk=1, container_name='run-1')
        get_container.return_value = container
        get_storage.return_value = storage
        list_container_blobs.side_effect = [
            ([], {1: {'blob_full_path': 'results/file.txt'}}),
            ([], {}),
        ]
        delete_blobs.return_value = ([], [])
        render_template.return_value = 'rendered'
        request = RequestFactory().post(
            '/',
            {
                'form-action': 'delete',
                'selected_options[]': ['1'],
            },
        )
        request.user = Mock(is_authenticated=True)

        response = container_view.__wrapped__(request, filezone_pk=1)

        self.assertEqual(response, 'rendered')
        delete_blobs.assert_called_once_with(
            blob_client=storage,
            blob_list=[{'blob_full_path': 'results/file.txt'}],
            container_name='run-1',
        )
