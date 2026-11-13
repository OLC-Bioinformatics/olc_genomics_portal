"""Tests for asynchronous data retrieval tasks."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from olc_webportalv2.data.models import DataRequest
from olc_webportalv2.data.tasks import get_raw_data


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
