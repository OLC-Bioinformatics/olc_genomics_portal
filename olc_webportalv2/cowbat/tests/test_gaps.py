from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from olc_webportalv2.cowbat.forms import CustomRunForm, RealTimeForm
from olc_webportalv2.cowbat.methods import BatchClass
from olc_webportalv2.cowbat.models import ResearchRun, SequencingRun
from olc_webportalv2.cowbat.tasks import rename_research_container_task
from olc_webportalv2.cowbat.utils import (
    normalize_research_run_name,
    normalize_run_name,
)
from olc_webportalv2.cowbat.views import (
    RunAutoCompleter,
    check_uploaded_seqids,
    find_percent_complete,
    validate_research_files,
)
from olc_webportalv2.users.models import User


class CowbatFormTests(TestCase):
    def test_custom_run_form_normalizes_name(self):
        form = CustomRunForm(data={
            'run_name': '191218-cal',
            'basic_assembly': True,
            'preprocess': False,
            'nextseq': False,
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['run_name'], '191218-cal')

    def test_custom_run_form_accepts_descriptive_run_suffix(self):
        form = CustomRunForm(data={
            'run_name': '261019-vl00772-test',
            'basic_assembly': False,
            'preprocess': False,
            'nextseq': False,
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['run_name'], '261019-vl00772-test')

    def test_custom_run_form_rejects_conflicting_options(self):
        form = CustomRunForm(data={
            'run_name': '191218-cal',
            'basic_assembly': True,
            'preprocess': True,
            'nextseq': False,
        })

        self.assertFalse(form.is_valid())
        self.assertIn('mutually exclusive', str(form.errors))

    def test_realtime_form_builds_choices_and_initial_values(self):
        run = SequencingRun(
            run_name='191218_CAL',
            realtime_strains={'SEQ-2': 'True', 'SEQ-1': 'False'},
            sample_plate={'SEQ-1': 'LAB-1'},
        )

        form = RealTimeForm(instance=run)

        self.assertEqual(form.choice_list, (('SEQ-1', 'SEQ-1'), ('SEQ-2', 'SEQ-2')))
        self.assertEqual(form.initials, ['SEQ-2'])
        self.assertEqual(form.fields['SEQ-1'].initial, 'LAB-1')

    def test_normalize_run_name(self):
        self.assertEqual(normalize_run_name(' 191218_CAL '), '191218-cal')
        self.assertIsNone(normalize_run_name(None))

    def test_normalize_research_run_name_enforces_container_format(self):
        self.assertEqual(normalize_research_run_name('260911_VL00772'), '260911-vl00772')
        self.assertEqual(normalize_research_run_name('260911_M01234'), '260911-m01234')
        self.assertEqual(normalize_research_run_name('260911-vl00772-1'), '260911-vl00772-1')
        self.assertEqual(normalize_research_run_name('260911-vl00772-2'), '260911-vl00772-2')
        self.assertEqual(normalize_research_run_name('260911-vl00772-3'), '260911-vl00772-3')
        self.assertEqual(
            normalize_research_run_name('260911-' + 'a' * 56),
            '260911-' + 'a' * 56,
        )
        self.assertEqual(
            normalize_research_run_name('261019-vl00772-test'),
            '261019-vl00772-test',
        )
        with self.assertRaises(ValueError):
            normalize_research_run_name('run-without-date')
        with self.assertRaises(ValueError):
            normalize_research_run_name('260911-' + 'a' * 57)


class CowbatMethodTests(TestCase):
    def test_validate_job_name_accepts_lowercase_valid_name(self):
        self.assertTrue(BatchClass.validate_job_name('run-1234'))

    def test_validate_job_name_rejects_invalid_names(self):
        invalid_names = ['Run-1234', 'abc', 'run--1234', 'run-1234-', 'run_1234']
        for name in invalid_names:
            with self.assertRaises(AttributeError):
                BatchClass.validate_job_name(name)


class CowbatUploadStateTests(TestCase):
    @mock.patch('olc_webportalv2.cowbat.views.BlockBlobService')
    def test_check_uploaded_seqids_separates_present_and_missing_reads(
        self, blob_service_class
    ):
        user = User.objects.create_user(username='cowbat-upload-test')
        run = SequencingRun.objects.create(
            user=user,
            run_name='191218_CAL',
            seqids=['SEQ-1', 'SEQ-2'],
        )
        blob_service_class.return_value.list_blobs.return_value = [
            mock.Mock(name='SEQ-1_R1.fastq'),
            mock.Mock(name='SEQ-1_R2.fastq'),
            mock.Mock(name='SEQ-2_R1.fastq'),
        ]
        blob_service_class.return_value.list_blobs.return_value[0].name = 'SEQ-1_R1.fastq'
        blob_service_class.return_value.list_blobs.return_value[1].name = 'SEQ-1_R2.fastq'
        blob_service_class.return_value.list_blobs.return_value[2].name = 'SEQ-2_R1.fastq'

        check_uploaded_seqids(run)

        run.refresh_from_db()
        self.assertEqual(run.uploaded_forward_reads, ['SEQ-1', 'SEQ-2'])
        self.assertEqual(run.uploaded_reverse_reads, ['SEQ-1'])
        self.assertEqual(run.reverse_reads_to_upload, ['SEQ-2'])

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_run_autocompleter_filters_by_query(self, storage_factory):
        ResearchRun.objects.create(run_name='191218-cal')
        ResearchRun.objects.create(run_name='201001-other')
        storage_factory.return_value.list_containers.side_effect = RuntimeError('storage unavailable')
        view = RunAutoCompleter()
        view.q = 'cal'

        self.assertEqual(view.get_list(), ['191218-cal'])

    def test_validate_research_files_reports_missing_pair(self):
        files = ['SAMPLE_R1.fastq.gz', 'OTHER_R1.fastq.gz', 'OTHER_R2.fastq.gz']

        seqids, errors = validate_research_files(mock.Mock(), 'run', files)

        self.assertEqual(seqids, ['OTHER'])
        self.assertIn('SAMPLE is missing R2.', errors)

    def test_validate_research_files_normalizes_illumina_lane_suffixes(self):
        files = [
            '2018-CAL-0033_S1_L001_R1_001.fastq.gz',
            '2018-CAL-0033_S1_L001_R2_001.fastq.gz',
        ]

        seqids, errors = validate_research_files(mock.Mock(), 'run', files)

        self.assertEqual(seqids, ['2018-CAL-0033'])
        self.assertEqual(errors, [])

    def test_validate_research_files_reports_duplicate_read(self):
        files = ['SAMPLE_R1_001.fastq.gz', 'SAMPLE_R1_002.fastq.gz', 'SAMPLE_R2.fastq.gz']

        seqids, errors = validate_research_files(mock.Mock(), 'run', files)

        self.assertEqual(seqids, ['SAMPLE'])
        self.assertIn('Duplicate R1 file for SAMPLE.', errors)

    def test_validate_research_files_compares_sample_sheet(self):
        storage = mock.Mock()
        storage.download_blob.side_effect = lambda container, name, path: open(path, 'w').write(
            '[Data]\nSample_ID,Sample_Name\nSAMPLE,Sample\nMISSING,Missing\n'
        )
        files = ['SAMPLE_R1.fastq.gz', 'SAMPLE_R2.fastq.gz', 'SampleSheet.csv']

        seqids, errors = validate_research_files(storage, 'run', files)

        self.assertEqual(seqids, ['SAMPLE'])
        self.assertTrue(any('MISSING' in error for error in errors))

    def test_validate_research_files_accepts_sample_sheet_header_variants(self):
        storage = mock.Mock()
        storage.download_blob.side_effect = lambda container, name, path: open(path, 'w').write(
            '[Header]\nIEMFileVersion,4\n[Data]\n\ufeffsample id,Sample Name\nSAMPLE,Sample\n'
        )
        files = ['SAMPLE_S1_L001_R1_001.fastq.gz', 'SAMPLE_S1_L001_R2_001.fastq.gz', 'SampleSheet.csv']

        seqids, errors = validate_research_files(storage, 'run', files)

        self.assertEqual(seqids, ['SAMPLE'])
        self.assertEqual(errors, [])


class CowbatWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cowbat-workflow-test',
            password='password',
        )
        self.client.login(
            username='cowbat-workflow-test',
            password='password',
        )

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_manage_research_assembly_lists_existing_files(self, storage_factory):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
        )
        storage_factory.return_value.list_files.return_value = [
            'SAMPLE_R1.fastq.gz',
            'SAMPLE_R2.fastq.gz',
        ]

        response = self.client.get(
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SAMPLE_R1.fastq.gz')
        self.assertContains(response, 'SAMPLE_R2.fastq.gz')

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_manage_research_assembly_hides_zero_byte_folder_markers(
        self, storage_factory
    ):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal-folders',
            container='240824-cal-folders',
        )
        storage_factory.return_value.list_file_entries.return_value = [
            {'name': 'reports', 'size': 0},
            {'name': 'reports/', 'size': 0},
            {'name': 'reports/result.txt', 'size': 20},
            {'name': 'README', 'size': 20},
        ]

        response = self.client.get(
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-blob-name="reports"')
        self.assertNotContains(response, 'data-blob-name="reports/"')
        self.assertContains(response, 'data-blob-name="reports/result.txt"')
        self.assertContains(response, 'data-blob-name="README"')

    @mock.patch('olc_webportalv2.cowbat.views.rename_research_container_task')
    def test_other_users_can_queue_run_rename(self, rename_task):
        owner = User.objects.create_user(username='cowbat-run-owner')
        run = SequencingRun.objects.create(
            user=owner,
            run_name='240824-cal',
            container='240824-cal',
        )

        response = self.client.post(
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            {'action': 'rename', 'run_name': '240824-new'},
        )

        self.assertRedirects(
            response,
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            fetch_redirect_response=False,
        )
        rename_task.apply_async.assert_called_once_with(
            args=[run.pk, '240824-cal', '240824-new'],
            queue='cowbat',
        )
        run.refresh_from_db()
        self.assertEqual(run.rename_status, 'queued')
        self.assertEqual(run.rename_target, '240824-new')

    def test_rename_status_returns_shared_run_state(self):
        other_user = User.objects.create_user(username='cowbat-status-owner')
        run = SequencingRun.objects.create(
            user=other_user,
            run_name='240824-cal',
            container='240824-cal',
            rename_status='queued',
            rename_target='240824-new',
        )

        response = self.client.get(
            reverse(
                'cowbat:research_assembly_rename_status',
                kwargs={'sequencing_run_pk': run.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'queued')
        self.assertEqual(response.json()['target_name'], '240824-new')

    @mock.patch('olc_webportalv2.cowbat.views.rename_research_container_task')
    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_rename_rejects_existing_blob_container(self, storage_factory, rename_task):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
        )
        storage = storage_factory.return_value
        storage.list_containers.return_value = ['240824-new']
        storage.list_files.return_value = []

        response = self.client.post(
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            {'action': 'rename', 'run_name': '240824-new'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'That container name is already in use.')
        rename_task.apply_async.assert_not_called()

    @mock.patch('olc_webportalv2.cowbat.views.rename_research_container_task')
    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_rename_rejects_processing_run(self, storage_factory, rename_task):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
            workflow_state='processing',
        )
        storage_factory.return_value.list_files.return_value = []

        response = self.client.post(
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            {'action': 'rename', 'run_name': '240824-new'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A run cannot be renamed after processing has started.')
        rename_task.apply_async.assert_not_called()

    @mock.patch('olc_webportalv2.cowbat.views.rename_research_container_task')
    def test_completed_run_can_be_renamed(self, rename_task):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
            status='Complete',
            workflow_state='complete',
        )

        response = self.client.post(
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            {'action': 'rename', 'run_name': '240824-new'},
        )

        self.assertRedirects(
            response,
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            fetch_redirect_response=False,
        )
        rename_task.apply_async.assert_called_once_with(
            args=[run.pk, '240824-cal', '240824-new'],
            queue='cowbat',
        )

    @mock.patch('olc_webportalv2.cowbat.tasks.get_research_storage')
    def test_rename_task_updates_model_after_storage_success(self, storage_factory):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
        )

        rename_research_container_task.run(run.pk, '240824-cal', '240824-new')

        storage_factory.return_value.rename_container.assert_called_once_with(
            '240824-cal', '240824-new'
        )
        run.refresh_from_db()
        self.assertEqual(run.run_name, '240824-new')
        self.assertEqual(run.container, '240824-new')
        self.assertEqual(run.rename_status, 'complete')
        self.assertEqual(run.rename_target, '240824-new')

    @mock.patch('olc_webportalv2.cowbat.tasks.get_research_storage')
    def test_rename_task_records_failure(self, storage_factory):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
        )
        storage_factory.return_value.rename_container.side_effect = RuntimeError('storage down')

        with self.assertRaises(RuntimeError):
            rename_research_container_task.run(run.pk, '240824-cal', '240824-new')

        run.refresh_from_db()
        self.assertEqual(run.rename_status, 'failed')
        self.assertEqual(run.rename_target, '240824-new')
        self.assertIn('Rename failed: storage down', run.errors)

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_manage_research_assembly_reuses_existing_run_by_name(self, storage_factory):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
        )

        response = self.client.get(
            reverse('cowbat:manage_assembly'),
            {'run_name': '240824-cal'},
        )

        self.assertRedirects(
            response,
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual(SequencingRun.objects.filter(run_name='240824-cal').count(), 1)

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_manage_research_assembly_creates_run_with_observability_defaults(
        self, storage_factory
    ):
        storage_factory.return_value.list_containers.return_value = ['000000-aac']

        response = self.client.get(
            reverse('cowbat:manage_assembly'),
            {'run_name': '000000-aac'},
        )

        run = SequencingRun.objects.get(run_name='000000-aac')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(run.workflow_state, 'uploading')
        self.assertEqual(run.failure_stage, '')
        self.assertEqual(run.task_queue, '')
        self.assertEqual(run.task_retry_count, 0)

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_delete_research_files_only_deletes_available_files(self, storage_factory):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
        )
        storage = storage_factory.return_value
        storage.list_files.return_value = ['SAMPLE_R1.fastq.gz']

        response = self.client.post(
            reverse('cowbat:delete_research_files', kwargs={'sequencing_run_pk': run.pk}),
            {'files': ['SAMPLE_R1.fastq.gz', 'not-present.fastq.gz']},
        )

        self.assertRedirects(
            response,
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
        )
        storage.delete_file.assert_called_once_with('240824-cal', 'SAMPLE_R1.fastq.gz')

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_manage_research_assembly_rejects_incomplete_read_pair(self, storage_factory):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
        )
        storage_factory.return_value.list_files.return_value = ['SAMPLE_R1.fastq.gz']

        response = self.client.post(
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            {
                'action': 'submit',
                'run_name': '240824-cal',
                'basic_assembly': '',
                'preprocess': '',
                'nextseq': '',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'At least one complete R1/R2 FASTQ pair is required.')

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_manage_research_assembly_submits_complete_read_pair(self, storage_factory):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='240824-cal',
            container='240824-cal',
        )
        storage_factory.return_value.list_files.return_value = [
            'SAMPLE_R1.fastq.gz',
            'SAMPLE_R2.fastq.gz',
        ]

        response = self.client.post(
            reverse('cowbat:manage_assembly_run', kwargs={'sequencing_run_pk': run.pk}),
            {
                'action': 'submit',
                'run_name': '240824-cal',
                'basic_assembly': 'on',
                'preprocess': '',
                'nextseq': '',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('cowbat:cowbat_processing', kwargs={'sequencing_run_pk': run.pk}),
        )
        run.refresh_from_db()
        self.assertEqual(run.seqids, ['SAMPLE'])
        self.assertEqual(run.status, 'Unprocessed')
        self.assertEqual(run.workflow_state, 'uploading')

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_upload_assembly_assigns_run_to_logged_in_user(self, storage_factory):
        response = self.client.post(
            reverse('cowbat:upload_assembly'),
            {
                'run_name': '240824_M12345',
                'basic_assembly': False,
                'preprocess': False,
                'nextseq': True,
            },
        )

        run = SequencingRun.objects.get(run_name='240824-m12345')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(run.user, self.user)
        self.assertEqual(run.nextseq, True)
        self.assertEqual(
            response.url,
            reverse('cowbat:upload_assembly_upload', kwargs={'sequencing_run_pk': run.pk}),
        )
        storage_factory.return_value.ensure_container.assert_called_once_with(
            '240824-m12345'
        )

    @mock.patch('olc_webportalv2.cowbat.views.get_research_storage')
    def test_upload_assembly_accepts_descriptive_run_suffix(self, storage_factory):
        response = self.client.post(
            reverse('cowbat:upload_assembly'),
            {
                'run_name': '261019-vl00772-test',
                'basic_assembly': False,
                'preprocess': False,
                'nextseq': True,
            },
        )

        run = SequencingRun.objects.get(run_name='261019-vl00772-test')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('cowbat:upload_assembly_upload', kwargs={'sequencing_run_pk': run.pk}),
        )
        storage_factory.return_value.ensure_container.assert_called_once_with(
            '261019-vl00772-test'
        )

    @mock.patch('olc_webportalv2.cowbat.views.run_cowbat_batch')
    def test_processing_starts_unprocessed_run(self, run_task):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='191218_CAL',
            status='Unprocessed',
        )

        response = self.client.get(
            reverse('cowbat:cowbat_processing', kwargs={'sequencing_run_pk': run.pk})
        )

        run.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run.status, 'Processing')
        self.assertEqual(run_task.apply_async.call_count, 1)

    def test_processing_saves_email(self):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='191218_CAL',
            status='Complete',
        )

        response = self.client.post(
            reverse('cowbat:cowbat_processing', kwargs={'sequencing_run_pk': run.pk}),
            {'email': 'assembly@example.com'},
        )

        run.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run.emails_array, ['assembly@example.com'])

    def test_retry_resets_run_status(self):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='191218_CAL',
            status='Error',
        )

        response = self.client.get(
            reverse('cowbat:retry_sequence_data_upload', kwargs={'sequencing_run_pk': run.pk})
        )

        run.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(run.status, 'Unprocessed')

    def test_other_users_can_access_processing_run(self):
        other_user = User.objects.create_user(username='cowbat-other-user')
        run = SequencingRun.objects.create(
            user=other_user,
            run_name='191218_CAL',
        )

        response = self.client.get(
            reverse('cowbat:cowbat_processing', kwargs={'sequencing_run_pk': run.pk})
        )

        self.assertEqual(response.status_code, 200)

    @mock.patch('olc_webportalv2.cowbat.views.batch.BatchServiceClient')
    @mock.patch('olc_webportalv2.cowbat.views.batch_auth.SharedKeyCredentials')
    def test_find_percent_complete_counts_report_files(
        self, credentials, batch_client_class
    ):
        run = SequencingRun(run_name='191218_CAL')
        batch_client_class.return_value.file.list_from_task.return_value = [
            mock.Mock(name='reports/one.txt'),
            mock.Mock(name='reports/two.txt'),
            mock.Mock(name='other.txt'),
        ]
        batch_client_class.return_value.file.list_from_task.return_value[0].name = 'reports/one.txt'
        batch_client_class.return_value.file.list_from_task.return_value[1].name = 'reports/two.txt'
        batch_client_class.return_value.file.list_from_task.return_value[2].name = 'other.txt'

        self.assertEqual(find_percent_complete(run), 7)

    @mock.patch('olc_webportalv2.cowbat.views.BlockBlobService')
    def test_upload_interop_posts_files_to_interop_path(self, blob_service_class):
        run = SequencingRun.objects.create(user=self.user, run_name='191218_CAL')
        upload = SimpleUploadedFile('metrics.bin', b'metrics')

        response = self.client.post(
            reverse('cowbat:upload_interop', kwargs={'sequencing_run_pk': run.pk}),
            {'file[0]': upload},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            blob_service_class.return_value.create_blob_from_bytes.call_count,
            1,
        )
        self.assertEqual(
            blob_service_class.return_value.create_blob_from_bytes.call_args[1]['blob_name'],
            'InterOp/metrics.bin',
        )

    @mock.patch('olc_webportalv2.cowbat.views.check_uploaded_seqids')
    def test_upload_sequence_data_get_checks_uploaded_reads(self, check_uploaded):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='191218_CAL',
            seqids=['SEQ-1'],
        )

        response = self.client.get(
            reverse('cowbat:upload_sequence_data', kwargs={'sequencing_run_pk': run.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(check_uploaded.call_count, 1)
        self.assertEqual(check_uploaded.call_args[1]['sequencing_run'].pk, run.pk)