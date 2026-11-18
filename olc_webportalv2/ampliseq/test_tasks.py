from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase

from olc_webportalv2.ampliseq.models import (
    AmpliSeqAzureTask,
    AmpliSeqRequest,
)
from olc_webportalv2.ampliseq.tasks import (
    check_for_task_completion,
    create_system_call,
    delete_pool_job,
    download_reports,
    download_results,
    run_ampliseq_batch,
    task_failed,
    task_succeeded,
)
from olc_webportalv2.users.models import User


class AmpliSeqCommandTests(SimpleTestCase):
    def test_command_includes_dada_options_and_metadata(self):
        request = SimpleNamespace(
            container_name='ampliseq-1',
            metadata=SimpleNamespace(name='metadata.csv'),
            max_len=250,
            min_len=100,
            max_ee=2,
            trunc_len_f=220,
            trunc_len_r=200,
            forward_primer='ACGT',
            reverse_primer='TGCA',
            taxonomy='dada',
            dada_ref_taxonomy='silva=132',
            qiime_ref_taxonomy='silva=138',
            classifier=None,
            exclude_taxa='chloroplast',
        )

        command = create_system_call(request)

        self.assertIn('--metadata /datadrive/ampliseq-1/metadata.csv', command)
        self.assertIn('--max_len 250', command)
        self.assertIn('--trunclenf 220', command)
        self.assertIn('--FW_primer ACGT', command)
        self.assertIn('--dada_ref_taxonomy silva=132', command)
        self.assertIn('--exclude_taxa chloroplast', command)
        self.assertNotIn('--skip_cutadapt', command)

    def test_custom_qiime_command_uses_classifier_and_skips_cutadapt(self):
        request = SimpleNamespace(
            container_name='ampliseq-custom',
            metadata=None,
            max_len=None,
            min_len=None,
            max_ee=None,
            trunc_len_f=None,
            trunc_len_r=None,
            forward_primer=None,
            reverse_primer=None,
            taxonomy='qiime_custom',
            dada_ref_taxonomy=None,
            qiime_ref_taxonomy=None,
            classifier=SimpleNamespace(name='classifier.qza'),
            exclude_taxa=None,
        )

        command = create_system_call(request)

        self.assertIn('--classifier /datadrive/ampliseq-custom/classifier.qza', command)
        self.assertIn('--skip_cutadapt', command)
        self.assertNotIn('--qiime_ref_taxonomy', command)


class AmpliSeqTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='ampliseq-task-test',
            password='password',
        )
        self.request = AmpliSeqRequest.objects.create(
            user=self.user,
            project_name='Test run',
            container_name='ampliseq-test',
            emails_array=['alerts@example.com'],
        )

    @mock.patch('olc_webportalv2.ampliseq.tasks.os.makedirs')
    @mock.patch('olc_webportalv2.ampliseq.tasks.generic_api_submit')
    def test_batch_submission_records_exit_code_tracking(
        self, generic_api_submit, makedirs
    ):
        run_ampliseq_batch.run(self.request.pk)

        generic_api_submit.assert_called_once()
        self.assertEqual(
            generic_api_submit.call_args.kwargs['analysis_type'], 'AmpliSeq'
        )
        task = AmpliSeqAzureTask.objects.get(ampliseq=self.request)
        self.assertEqual(
            task.exit_code_file,
            'olc_webportalv2/media/ampliseq-1/exit_codes.txt',
        )
        makedirs.assert_called_once()

    def test_task_completion_waits_for_every_cloud_task(self):
        tracking_task = AmpliSeqAzureTask.objects.create(
            ampliseq=self.request,
            exit_code_file='/tmp/exit_codes.txt',
        )
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [
            SimpleNamespace(state='completed'),
            SimpleNamespace(state='running'),
        ]

        completed, request = check_for_task_completion(
            task=tracking_task,
            batch_client=batch_client,
        )

        self.assertFalse(completed)
        self.assertEqual(request, self.request)

    def test_task_list_error_marks_request_and_removes_tracker(self):
        tracking_task = AmpliSeqAzureTask.objects.create(
            ampliseq=self.request,
            exit_code_file='/tmp/exit_codes.txt',
        )
        batch_client = mock.Mock()
        batch_client.task.list.side_effect = RuntimeError('Batch unavailable')

        completed, _request = check_for_task_completion(
            task=tracking_task,
            batch_client=batch_client,
        )

        self.assertTrue(completed)
        self.assertFalse(
            AmpliSeqAzureTask.objects.filter(pk=tracking_task.pk).exists()
        )
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, 'Error')
        self.assertEqual(self.request.error_list, ['Batch unavailable'])

    def test_pool_and_job_are_deleted_after_checking_exit_codes(self):
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [
            SimpleNamespace(execution_info=SimpleNamespace(exit_code=0)),
            SimpleNamespace(execution_info=SimpleNamespace(exit_code=1)),
        ]

        self.assertFalse(delete_pool_job(batch_client, 'ampliseq-test'))

        batch_client.job.delete.assert_called_once_with(
            job_id='ampliseq-test'
        )
        batch_client.pool.delete.assert_called_once_with(
            pool_id='ampliseq-test'
        )

    @mock.patch('olc_webportalv2.ampliseq.tasks.get_storage_client')
    def test_reports_are_downloaded_from_the_expected_blob_paths(
        self, get_storage_client
    ):
        download_reports(self.request)

        storage = get_storage_client.return_value
        self.assertEqual(storage.download_file.call_count, 2)
        self.assertIn(
            'ampliseq-test_execution_report.html',
            storage.download_file.call_args_list[0].args[1],
        )
        self.assertIn(
            'ampliseq-test_execution_timeline.html',
            storage.download_file.call_args_list[1].args[1],
        )

    @mock.patch('olc_webportalv2.ampliseq.tasks.os.remove')
    @mock.patch('olc_webportalv2.ampliseq.tasks.shutil.rmtree')
    @mock.patch('olc_webportalv2.ampliseq.tasks.shutil.make_archive')
    @mock.patch('olc_webportalv2.ampliseq.tasks.os.makedirs')
    @mock.patch('olc_webportalv2.ampliseq.tasks.get_storage_client')
    def test_results_are_archived_uploaded_and_linked(
        self,
        get_storage_client,
        makedirs,
        make_archive,
        rmtree,
        remove,
    ):
        storage = get_storage_client.return_value
        storage.list_objects.return_value = [
            SimpleNamespace(
                name='ampliseq-test/results2/taxonomy/table.tsv'
            )
        ]
        storage.create_transfer_url.return_value = 'https://results'

        download_results(self.request)

        self.request.refresh_from_db()
        self.assertEqual(
            self.request.results_download_link, 'https://results'
        )
        storage.download_file.assert_called_once()
        storage.upload_file.assert_called_once()
        self.assertEqual(storage.create_transfer_url.call_args.kwargs['name'], 'ampliseq-test.zip')
        self.assertEqual(makedirs.call_count, 2)
        make_archive.assert_called_once()
        rmtree.assert_called_once()
        remove.assert_called_once()

    @mock.patch('olc_webportalv2.ampliseq.tasks.os.makedirs')
    @mock.patch('olc_webportalv2.ampliseq.tasks.get_storage_client')
    def test_missing_results_raise_instead_of_returning_a_link(
        self, get_storage_client, makedirs
    ):
        get_storage_client.return_value.list_objects.return_value = []

        with self.assertRaises(FileNotFoundError):
            download_results(self.request)

        self.request.refresh_from_db()
        self.assertEqual(self.request.results_download_link, '')
        makedirs.assert_called_once()

    @mock.patch('olc_webportalv2.ampliseq.tasks.send_email')
    @mock.patch('olc_webportalv2.ampliseq.tasks.download_results')
    @mock.patch('olc_webportalv2.ampliseq.tasks.download_reports')
    @mock.patch('olc_webportalv2.ampliseq.tasks.create_sas_urls')
    def test_successful_task_publishes_links_and_notifies_user(
        self,
        create_sas_urls,
        download_reports_mock,
        download_results_mock,
        send_email,
    ):
        create_sas_urls.return_value = ('report', 'trace', 'timeline')

        task_succeeded(self.request)

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, 'Complete')
        self.assertEqual(self.request.execution_report_download_link, 'report')
        download_reports_mock.assert_called_once_with(ampliseq=self.request)
        download_results_mock.assert_called_once_with(ampliseq=self.request)
        send_email.assert_called_once()
        self.assertEqual(
            send_email.call_args.kwargs['recipient'], 'alerts@example.com'
        )

    @mock.patch('olc_webportalv2.ampliseq.tasks.send_email')
    def test_failed_task_saves_error_status_and_notifies_user(self, send_email):
        self.request.error_list = ['Task exited with status 1']

        task_failed(self.request)

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, 'Error')
        self.assertIn('Task exited with status 1', send_email.call_args.kwargs['body'])
