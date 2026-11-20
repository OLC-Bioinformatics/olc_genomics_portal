from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import TestCase as DjangoTestCase

from olc_webportalv2.cowsnphr.models import (
    ContainerName,
    COWSNPhRAzureTask,
    COWSNPhRRequest,
)
from olc_webportalv2.cowsnphr.tasks import (
    check_for_task_completion,
    check_cowsnphr_tasks,
    create_system_call,
    download_folders,
    find_query,
    find_ref,
    generate_archive_download_link,
    populate_models,
    read_spreadsheet,
    refresh_container_names,
    run_cowsnphr_batch,
    task_failed,
    task_succeeded,
)
from olc_webportalv2.users.models import User


class COWSNPhRTaskLogicTests(TestCase):
    def test_command_includes_optional_mask_only_when_present(self):
        command = create_system_call('cowsnphr-run', 'ref/mask.bed')
        command_without_mask = create_system_call('cowsnphr-run', '')

        self.assertIn('-m /datadrive/cowsnphr-run/ref/mask.bed', command)
        self.assertIn('-g -w /datadrive', command)
        self.assertNotIn(' -m ', command_without_mask)

    @patch('olc_webportalv2.cowsnphr.tasks.FileLocate')
    def test_reference_lookup_prefers_processed_data(self, file_locate):
        request = SimpleNamespace(ref='2015-SEQ-0711')
        reference = [{'blob_name': '2015-SEQ-0711.fasta'}]
        file_locate.return_value.main.return_value = (
            {'processed-data': reference},
            {},
        )

        result = find_ref(request)

        self.assertEqual(result, reference)
        file_locate.assert_called_once()

    @patch('olc_webportalv2.cowsnphr.tasks.FileLocate')
    def test_reference_lookup_uses_best_assembly_fallback(self, file_locate):
        request = SimpleNamespace(ref='2015-SEQ-0711')
        best_assembly = {
            'blob_name': 'BestAssemblies/2015-SEQ-0711.fasta'
        }
        file_locate.return_value.main.side_effect = [
            ({}, {}),
            (
                {
                    'assembly-run': [
                        {'blob_name': 'other/reference.fasta'},
                        best_assembly,
                    ]
                },
                {},
            ),
        ]

        self.assertEqual(find_ref(request), [best_assembly])
        self.assertEqual(file_locate.call_count, 2)

    @patch('olc_webportalv2.cowsnphr.tasks.FileLocate')
    def test_missing_reference_records_error(self, file_locate):
        request = SimpleNamespace(
            ref='2015-SEQ-0711',
            seqid_errors=[],
            save=Mock(),
            user=SimpleNamespace(language='fr'),
        )
        file_locate.return_value.main.return_value = ({}, {})

        self.assertIsNone(find_ref(request))

        self.assertEqual(request.status, 'Error')
        self.assertIn(
            'Impossible de trouver un fichier FASTA',
            request.seqid_errors[0],
        )
        self.assertIn('2015-SEQ-0711', request.seqid_errors[0])
        request.save.assert_called_once()

    @patch('olc_webportalv2.cowsnphr.tasks.FileLocate')
    def test_query_lookup_requires_a_forward_and_reverse_read(self, file_locate):
        request = SimpleNamespace(
            seqids=['2015-SEQ-0711'],
            seqid_errors=[],
            save=Mock(),
            user=SimpleNamespace(language='fr'),
        )
        file_locate.return_value.main.return_value = (
            {
                'run-1': [
                    {'blob_name': '2015-SEQ-0711_R1.fastq.gz'},
                    {'blob_name': '2015-SEQ-0711_R2.fastq.gz'},
                ]
            },
            {},
        )

        result = find_query(request)

        self.assertEqual(len(result), 2)
        self.assertEqual(request.seqid_errors, [])
        request.save.assert_not_called()

    @patch('olc_webportalv2.cowsnphr.tasks.FileLocate')
    def test_query_lookup_reports_missing_read_pair(self, file_locate):
        request = SimpleNamespace(
            seqids=['2015-SEQ-0711'],
            seqid_errors=[],
            save=Mock(),
            user=SimpleNamespace(language='fr'),
        )
        file_locate.return_value.main.return_value = (
            {'run-1': [{'blob_name': '2015-SEQ-0711_R1.fastq.gz'}]},
            {},
        )

        self.assertIsNone(find_query(request))

        self.assertEqual(request.status, 'Error')
        self.assertIn(
            'Impossible de trouver un ou plusieurs fichiers .fastq.gz',
            request.seqid_errors[0],
        )
        self.assertIn('2015-SEQ-0711', request.seqid_errors[0])
        request.save.assert_called_once()

    @patch(
        'olc_webportalv2.common.email_i18n.get_recipient_language',
        return_value='en-ca',
    )
    @patch('olc_webportalv2.cowsnphr.tasks.send_email')
    def test_successful_task_saves_status_and_notifies_subscribers(
        self, send_email, get_recipient_language
    ):
        request = SimpleNamespace(
            status='Processing',
            emails_array=['alerts@example.com'],
            project_name='Run',
            user='analyst',
            archive_download_link='https://archive',
            save=Mock(),
        )

        task_succeeded(request)

        self.assertEqual(request.status, 'Complete')
        request.save.assert_called_once()
        send_email.assert_called_once()
        self.assertEqual(
            send_email.call_args.kwargs['recipient'], 'alerts@example.com'
        )
        get_recipient_language.assert_called_once_with('alerts@example.com')

    @patch(
        'olc_webportalv2.common.email_i18n.get_recipient_language',
        return_value='en-ca',
    )
    @patch('olc_webportalv2.cowsnphr.tasks.send_email')
    def test_failed_task_saves_status_and_reports_errors(
        self, send_email, get_recipient_language
    ):
        request = SimpleNamespace(
            status='Processing',
            emails_array=['alerts@example.com'],
            project_name='Run',
            user='analyst',
            error_list=['Batch task failed'],
            save=Mock(),
        )

        task_failed(request)

        self.assertEqual(request.status, 'Error')
        request.save.assert_called_once()
        self.assertIn(
            'Batch task failed', send_email.call_args.kwargs['body']
        )
        get_recipient_language.assert_called_once_with('alerts@example.com')

    @patch('olc_webportalv2.cowsnphr.tasks.COWSNPhRRequest.objects.get')
    def test_unusable_batch_node_is_rebooted(self, get_request):
        request = SimpleNamespace(pk=3, container_name='cowsnphr-run')
        get_request.return_value = request
        task = SimpleNamespace(cowsnphr=SimpleNamespace(pk=3))
        batch_client = Mock()
        batch_client.task.list.return_value = [
            SimpleNamespace(state='running')
        ]
        batch_client.pool.list.return_value = [
            SimpleNamespace(id='cowsnphr-run', resize_errors=[])
        ]
        batch_client.compute_node.list.return_value = [
            SimpleNamespace(id='node-1', state='unusable')
        ]

        result = check_for_task_completion(task, batch_client)

        self.assertEqual(result, (False, request))
        batch_client.compute_node.reboot.assert_called_once_with(
            pool_id='cowsnphr-run',
            node_id='node-1',
            node_reboot_option='requeue',
        )


class COWSNPhRDatabaseTaskTests(DjangoTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cowsnphr-task-test')

    def create_request(self, **kwargs):
        return COWSNPhRRequest.objects.create(
            user=self.user,
            project_name='COWSNPhR run',
            container_name='cowsnphr-run',
            **kwargs,
        )

    @patch('olc_webportalv2.cowsnphr.tasks.os.makedirs')
    @patch('olc_webportalv2.cowsnphr.tasks.generic_api_submit')
    @patch('olc_webportalv2.cowsnphr.tasks.create_system_call')
    @patch(
        'olc_webportalv2.cowsnphr.tasks.container_sanity',
        return_value=(True, True, 'ref/mask.bed'),
    )
    def test_batch_submission_records_a_tracker_for_uploaded_inputs(
        self, container_sanity_mock, create_command, submit, makedirs
    ):
        request = self.create_request(seqids=[])
        create_command.return_value = 'cowsnphr command'

        run_cowsnphr_batch.run(request.pk)

        container_sanity_mock.assert_called_once_with(
            container_name='cowsnphr-run'
        )
        submit.assert_called_once_with(
            command='cowsnphr command',
            container_name='cowsnphr-run',
            vm_size='Standard_NV18ads_A10_v5',
            analysis_type='COWSNPhR',
            unique_id='FoodPort',
        )
        self.assertTrue(
            COWSNPhRAzureTask.objects.filter(cowsnphr=request).exists()
        )
        makedirs.assert_called_once()

    @patch('olc_webportalv2.cowsnphr.tasks.generic_api_submit')
    @patch(
        'olc_webportalv2.cowsnphr.tasks.container_sanity',
        return_value=(False, True, ''),
    )
    def test_batch_submission_marks_missing_fastq_as_error(
        self, container_sanity_mock, submit
    ):
        request = self.create_request(seqids=[])

        run_cowsnphr_batch.run(request.pk)

        request.refresh_from_db()
        self.assertEqual(request.status, 'Error')
        self.assertIn('FASTQ files could not be located', request.error_list[0])
        submit.assert_not_called()
        self.assertFalse(
            COWSNPhRAzureTask.objects.filter(cowsnphr=request).exists()
        )
        container_sanity_mock.assert_called_once()

    def test_refresh_container_names_replaces_stale_names(self):
        ContainerName.objects.create(container_name='old-run')

        with patch(
            'olc_webportalv2.cowsnphr.tasks.find_containers',
            return_value=['new-run', 'shared-run'],
        ):
            refresh_container_names.run()

        self.assertQuerySetEqual(
            ContainerName.objects.order_by('container_name').values_list(
                'container_name', flat=True
            ),
            ['new-run', 'shared-run'],
            transform=str,
        )

    def test_tabular_spreadsheet_omits_unnamed_columns(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / 'summary.tsv'
            report_path.write_text(
                'sample\tcount\tUnnamed: 0\nA\t2\tignored\n',
                encoding='utf-8',
            )

            report, headers, padding = read_spreadsheet(str(report_path))

        self.assertEqual(headers, ['sample', 'count'])
        self.assertEqual(report, {0: {'sample': 'A', 'count': 2}})
        self.assertIsNone(padding)

    def test_download_folders_fetches_only_report_folders(self):
        request = SimpleNamespace(
            project_name='COWSNPhR run',
            container_name='cowsnphr-run',
        )
        storage = Mock()
        storage.list_objects.return_value = [
            SimpleNamespace(name=f'fastq/{folder}/report.txt')
            for folder in (
                'alignments',
                'snv_matrix',
                'summary_tables',
                'tree_files',
                'vcf_files',
                'unrelated',
            )
        ]

        with (
            patch(
                'olc_webportalv2.cowsnphr.tasks.get_storage_client',
                return_value=storage,
            ),
            patch('olc_webportalv2.cowsnphr.tasks.os.makedirs'),
        ):
            result = download_folders(request)

        self.assertEqual(result[0], storage)
        self.assertEqual(result[2], 'cowsnphr-run')
        self.assertEqual(storage.download_file.call_count, 5)

    def test_populate_models_loads_all_reports_and_saves_trees(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for folder, filename, content in (
                ('alignments', 'alignment.fasta', '>a\nACGT\n'),
                ('tree_files', 'best_tree.tre', '(a,b);\n'),
            ):
                target = root / folder
                target.mkdir()
                (target / filename).write_text(content, encoding='utf-8')
            for filename in (
                'aa_snv_sorted_table.xlsx',
                'assembly_report.tsv',
                'contig_summary.tsv',
                'nt_snv_sorted_table.xlsx',
                'snv_matrix.tsv',
                'snv_summary.tsv',
            ):
                target = root / 'summary_tables'
                target.mkdir(exist_ok=True)
                (target / filename).touch()
            request = Mock()
            request.pk = 7
            report_values = [
                ({0: ['aa']}, ['aa'], [0]),
                ({0: {'assembly': 'ok'}}, ['assembly'], None),
                ({0: {'contig': 'ok'}}, ['contig'], None),
                ({0: ['nt']}, ['nt'], [0]),
                ({0: {'matrix': 'ok'}}, ['matrix'], None),
                ({0: {'summary': 'ok'}}, ['summary'], None),
            ]

            with patch(
                'olc_webportalv2.cowsnphr.tasks.read_spreadsheet',
                side_effect=report_values,
            ) as read_report:
                populate_models(request, str(root))

        self.assertEqual(request.alignment, '>a<br>ACGT<br>')
        self.assertEqual(request.phylogenetic_tree, '(a,b);<br>')
        self.assertEqual(request.assembly_report, {0: {'assembly': 'ok'}})
        self.assertEqual(request.nucleotide_summary_table, {0: ['nt']})
        self.assertEqual(read_report.call_count, 6)
        request.save.assert_called_once()

    def test_archive_link_uploads_file_and_requests_eight_day_url(self):
        storage = Mock()
        storage.create_transfer_url.return_value = 'archive-url'

        link = generate_archive_download_link(
            storage,
            'cowsnphr-output',
            '/tmp/run.zip',
        )

        self.assertEqual(link, 'archive-url')
        storage.ensure_container.assert_called_once_with('cowsnphr-output')
        storage.upload_file.assert_called_once_with(
            'cowsnphr-output',
            'run.zip',
            '/tmp/run.zip',
        )
        storage.create_transfer_url.assert_called_once_with(
            'cowsnphr-output',
            'download',
            name='run.zip',
            expires_in_seconds=8 * 24 * 60 * 60,
        )

    @patch('olc_webportalv2.cowsnphr.tasks.task_succeeded')
    @patch('olc_webportalv2.cowsnphr.tasks.post_processing')
    @patch(
        'olc_webportalv2.cowsnphr.tasks.delete_pool_job',
        return_value=True,
    )
    @patch(
        'olc_webportalv2.cowsnphr.tasks.check_for_task_completion',
        return_value=(True, None),
    )
    @patch('olc_webportalv2.cowsnphr.tasks.create_batch_client')
    def test_completion_polls_and_removes_tracker(
        self,
        create_batch_client,
        check_completion,
        delete_resources,
        post_processing_mock,
        task_succeeded_mock,
    ):
        request = self.create_request()
        tracker = COWSNPhRAzureTask.objects.create(
            cowsnphr=request,
            exit_code_file='/tmp/exit_codes.txt',
        )
        check_completion.return_value = (True, request)

        check_cowsnphr_tasks.run()

        delete_resources.assert_called_once_with(
            batch_client=create_batch_client.return_value,
            batch_job_name='cowsnphr-run',
        )
        post_processing_mock.assert_called_once_with(cowsnphr=request)
        task_succeeded_mock.assert_called_once_with(cowsnphr=request)
        self.assertFalse(
            COWSNPhRAzureTask.objects.filter(pk=tracker.pk).exists()
        )
