import os
import smtplib
from unittest import mock
from unittest.mock import mock_open

from django.test import TestCase

from olc_webportalv2.geneseekr.models import (
    GeneSeekrAzureRequest,
    GeneSeekrRequest,
    NearestNeighbors,
)
from olc_webportalv2.geneseekr.tasks import (
    _create_benchmark_geneseekr_command,
    run_geneseekr,
    run_nearest_neighbors,
    send_email,
)
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.users.models import User


class GeneSeekrTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='geneseekr-task-test',
            password='password',
        )
        SequenceData.objects.create(
            seqid='2015-SEQ-0711',
            quality='Pass',
            genus='Listeria',
            subspecies='',
        )

    def test_normal_request_uploads_query_and_submits_batch_job(self):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711'],
            query_sequence='>query\nACGT',
        )

        with (
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.os.path.isdir',
                return_value=True,
            ),
            mock.patch(
                'builtins.open',
                mock_open(),
            ) as open_file,
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.get_storage_client'
            ) as get_storage,
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.generic_api_submit'
            ) as submit,
        ):
            run_geneseekr.run(request.pk)

        open_file().write.assert_called_once_with('>query\nACGT')
        get_storage.return_value.ensure_container.assert_called_once_with(
            f'geneseekr-{request.pk}'
        )
        get_storage.return_value.upload_file.assert_called_once()
        submission = submit.call_args.kwargs
        self.assertEqual(
            submission['input_file_pattern'],
            ['processed-data/2015-SEQ-0711.fasta sequences'],
        )
        self.assertEqual(submission['vm_size'], 'Standard_D4s_v3')
        self.assertIn('GeneSeekr blastn', submission['command'])
        self.assertTrue(
            GeneSeekrAzureRequest.objects.filter(
                geneseekr_request=request,
                exit_code_file='NA',
            ).exists()
        )

    def test_large_request_stages_a_zip_before_batch_submission(self):
        seqids = [f'2024-SEQ-{index:04d}' for index in range(51)]
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            seqids=seqids,
            query_sequence='>query\nACGT',
        )

        with (
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.os.path.isdir',
                return_value=True,
            ),
            mock.patch('builtins.open', mock_open()),
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.get_storage_client'
            ),
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.zip_files',
                return_value='inputs.zip',
            ) as zip_files,
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.generic_api_submit'
            ) as submit,
        ):
            run_geneseekr.run(request.pk)

        zip_files.assert_called_once_with(
            seqids=seqids,
            target_folder=f'geneseekr-{request.pk}',
            container_name='processed-data',
        )
        submission = submit.call_args.kwargs
        self.assertEqual(
            submission['input_file_pattern'],
            ['temporary-storage/inputs.zip'],
        )
        self.assertEqual(submission['vm_size'], 'Standard_D16s_v3')
        self.assertIn('unzip', submission['command'])

    def test_benchmark_request_records_the_selected_database_version(self):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            query_sequence='>query\nACGT',
            benchmark='bds-listeria',
        )
        download = {
            'benchmark_name': 'bds-listeria',
            'benchmark_version': '2.1.0',
            'blob_name': 'bds-listeria-2.1.0.zip',
            'content_length': 100,
            'sas_url': 'https://storage.example/benchmark',
        }

        with (
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.os.path.isdir',
                return_value=True,
            ),
            mock.patch('builtins.open', mock_open()),
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.get_storage_client'
            ),
            mock.patch(
                'olc_webportalv2.geneseekr.tasks._create_benchmark_download',
                return_value=download,
            ),
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.generic_api_submit'
            ) as submit,
        ):
            run_geneseekr.run(request.pk)

        request.refresh_from_db()
        self.assertEqual(
            request.benchmark_versions,
            {'bds-listeria': '2.1.0'},
        )
        self.assertEqual(
            submit.call_args.kwargs['input_file_pattern'],
            [],
        )
        self.assertEqual(
            submit.call_args.kwargs['vm_size'],
            'Standard_D32s_v3',
        )
        self.assertIn(
            'curl --fail --location --retry 5',
            submit.call_args.kwargs['command'],
        )

    def test_benchmark_command_quotes_remote_values_and_verifies_archive(self):
        command = _create_benchmark_geneseekr_command(
            benchmark_download={
                'benchmark_name': 'bds-listeria',
                'blob_name': 'benchmark file.zip',
                'content_length': 123,
                'sas_url': 'https://storage.example/file?a=1&b=2',
            },
            container_name='geneseekr-run',
        )

        self.assertIn('unzip -tq', command)
        self.assertIn('123c', command)
        self.assertIn('bds-listeria_seqids.txt', command)
        self.assertIn('sort -u', command)
        self.assertIn('GeneSeekr blastn', command)

    def test_nearest_neighbor_request_without_input_is_marked_error(self):
        request = NearestNeighbors.objects.create(
            user=self.user,
            number_neighbors=5,
        )

        with (
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.os.path.isdir',
                return_value=True,
            ),
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.get_storage_client'
            ),
            mock.patch(
                'olc_webportalv2.geneseekr.tasks.capture_exception'
            ) as capture_exception,
        ):
            run_nearest_neighbors.run(request.pk)

        request.refresh_from_db()
        self.assertEqual(request.status, 'Error')
        self.assertIsInstance(
            capture_exception.call_args.args[0],
            ValueError,
        )

    @mock.patch.dict(
        os.environ,
        {'EMAIL_HOST_USER': 'sender', 'EMAIL_HOST_PASSWORD': 'secret'},
    )
    @mock.patch('olc_webportalv2.geneseekr.tasks.smtplib.SMTP')
    def test_email_sender_uses_tls_and_closes_connection(self, smtp):
        server = smtp.return_value

        send_email('Subject', 'Body', 'recipient@example.com')

        server.starttls.assert_called_once_with()
        server.login.assert_called_once_with(
            user='sender',
            password='secret',
        )
        server.sendmail.assert_called_once()
        server.quit.assert_called_once_with()

    @mock.patch.dict(
        os.environ,
        {'EMAIL_HOST_USER': 'sender', 'EMAIL_HOST_PASSWORD': 'secret'},
    )
    @mock.patch('olc_webportalv2.geneseekr.tasks.sleep')
    @mock.patch('olc_webportalv2.geneseekr.tasks.smtplib.SMTP')
    def test_email_sender_retries_access_denied(self, smtp, sleep):
        first_server = mock.Mock()
        second_server = mock.Mock()
        first_server.sendmail.side_effect = smtplib.SMTPDataError(
            554, b'Access denied'
        )
        smtp.side_effect = [first_server, second_server]

        send_email('Subject', 'Body', 'recipient@example.com')

        sleep.assert_called_once_with(5)
        first_server.quit.assert_called_once_with()
        second_server.sendmail.assert_called_once()
        second_server.quit.assert_called_once_with()
