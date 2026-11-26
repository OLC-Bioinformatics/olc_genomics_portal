"""Tests for asynchronous data retrieval tasks."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from olc_webportalv2.data.models import DataRequest
from olc_webportalv2.data.tasks import get_assembled_data, get_raw_data


class RawDataTaskTests(TestCase):
    """Verify FASTQ retrieval request state updates."""

    def test_missing_fastqs_are_saved_and_mark_request_as_warning(self):
        """Persist missing sequence IDs and report a partial result."""
        user = get_user_model().objects.create_user(username='data-task-test')
        data_request = DataRequest.objects.create(
            user=user,
            seqids=['2015-SEQ-0711', '2015-SEQ-0712'],
            request_type='FASTQ',
            status='Processing',
        )
        storage = SimpleNamespace(
            list_containers=lambda: [SimpleNamespace(name='230101-a')],
            list_objects=lambda _container: [
                SimpleNamespace(name='2015-SEQ-0711_R1.fastq.gz')
            ],
            download_file=lambda *_args: None,
            ensure_container=lambda *_args: None,
            upload_file=lambda *_args: None,
            create_transfer_url=lambda *_args, **_kwargs: 'https://download',
        )

        with (
            patch(
                'olc_webportalv2.data.tasks.get_storage_client',
                return_value=storage,
            ),
            patch(
                'olc_webportalv2.data.tasks.os.path.isdir', return_value=True
            ),
            patch('olc_webportalv2.data.tasks.shutil.make_archive'),
            patch('olc_webportalv2.data.tasks.shutil.rmtree'),
            patch('olc_webportalv2.data.tasks.os.remove'),
        ):
            get_raw_data.run(data_request.pk)

        data_request.refresh_from_db()
        self.assertEqual(data_request.missing_seqids, ['2015-SEQ-0712'])
        self.assertEqual(data_request.status, 'Warning')

    def test_matching_fastqs_are_archived_and_request_is_completed(self):
        user = get_user_model().objects.create_user(username='data-fastq-test')
        data_request = DataRequest.objects.create(
            user=user,
            seqids=['2015-SEQ-0711'],
            request_type='FASTQ',
            status='Processing',
        )
        storage = SimpleNamespace(
            list_containers=lambda: [
                SimpleNamespace(name='230101-a'),
                SimpleNamespace(name='230101-b-output'),
            ],
            list_objects=lambda _container: [
                SimpleNamespace(name='2015-SEQ-0711_R1.fastq.gz'),
                SimpleNamespace(name='unrelated.txt'),
            ],
            download_file=lambda *_args: None,
            ensure_container=lambda *_args: None,
            upload_file=lambda *_args: None,
            create_transfer_url=lambda *_args, **_kwargs: 'https://download',
        )

        with (
            patch(
                'olc_webportalv2.data.tasks.get_storage_client',
                return_value=storage,
            ),
            patch('olc_webportalv2.data.tasks.os.makedirs'),
            patch('olc_webportalv2.data.tasks.shutil.make_archive'),
            patch('olc_webportalv2.data.tasks.shutil.rmtree'),
            patch('olc_webportalv2.data.tasks.os.remove'),
        ):
            get_raw_data.run(data_request.pk)

        data_request.refresh_from_db()
        self.assertEqual(data_request.missing_seqids, [])
        self.assertEqual(data_request.status, 'Complete')
        self.assertEqual(data_request.download_link, 'https://download')


class AssembledDataTaskTests(TestCase):
    def create_request(self, seqids):
        user = get_user_model().objects.create_user(
            username=f'assembled-data-{len(seqids)}'
        )
        return DataRequest.objects.create(
            user=user,
            seqids=seqids,
            request_type='FASTA',
            status='Processing',
        )

    def test_matching_fastas_are_archived_and_request_is_completed(self):
        data_request = self.create_request(['2015-SEQ-0711'])
        storage = SimpleNamespace(
            list_objects=lambda _container: [
                SimpleNamespace(name='2015-SEQ-0711.fasta'),
                SimpleNamespace(name='2015-SEQ-0712.fasta.gz'),
            ],
            download_file=Mock(),
            ensure_container=lambda *_args: None,
            upload_file=lambda *_args: None,
            create_transfer_url=lambda *_args, **_kwargs: 'https://fasta',
        )

        with (
            patch(
                'olc_webportalv2.data.tasks.get_storage_client',
                return_value=storage,
            ),
            patch('olc_webportalv2.data.tasks.os.makedirs'),
            patch('olc_webportalv2.data.tasks.shutil.make_archive'),
            patch('olc_webportalv2.data.tasks.shutil.rmtree'),
            patch('olc_webportalv2.data.tasks.os.remove'),
        ):
            get_assembled_data.run(data_request.pk)

        data_request.refresh_from_db()
        self.assertEqual(data_request.status, 'Complete')
        self.assertEqual(data_request.download_link, 'https://fasta')
        storage.download_file.assert_called_once()

    @patch('olc_webportalv2.data.tasks.capture_exception')
    @patch('olc_webportalv2.data.tasks.get_storage_client')
    def test_storage_failure_marks_assembled_request_as_error(
        self, get_storage_client, capture_exception
    ):
        data_request = self.create_request(['2015-SEQ-0711'])
        get_storage_client.side_effect = RuntimeError('storage unavailable')
        work_dir = f'olc_webportalv2/media/data_request_{data_request.pk}'

        with (
            patch('olc_webportalv2.data.tasks.os.makedirs'),
            patch(
                'olc_webportalv2.data.tasks.shutil.rmtree'
            ) as remove_work_dir,
            patch('olc_webportalv2.data.tasks.os.remove') as remove_archive,
        ):
            get_assembled_data.run(data_request.pk)

        data_request.refresh_from_db()
        self.assertEqual(data_request.status, 'Error')
        capture_exception.assert_called_once()
        remove_work_dir.assert_called_once_with(work_dir)
        remove_archive.assert_called_once_with(f'{work_dir}.zip')
