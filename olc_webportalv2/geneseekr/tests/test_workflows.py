import os
import tempfile
from unittest import mock
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.common.batch_client import BatchServiceError
from olc_webportalv2.cowbat.tasks import (
    check_amr_summary_tasks,
    check_tree_tasks,
    download_container,
)
from olc_webportalv2.geneseekr.tasks import (
    _create_benchmark_download,
    run_amr_summary,
    run_mash,
)
from olc_webportalv2.geneseekr.models import (
    AMRAzureRequest,
    AMRSummary,
    GeneSeekrRequest,
    NearestNeighbors,
    ProkkaRequest,
    TopBlastHit,
    Tree,
    TreeAzureRequest,
)
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.users.models import User


class GeneSeekrWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='geneseekr-workflow-test',
            password='password',
        )
        SequenceData.objects.create(
            seqid='2015-SEQ-0711',
            quality='Pass',
            genus='Listeria',
            subspecies='',
        )
        SequenceData.objects.create(
            seqid='2015-SEQ-0712',
            quality='Pass',
            genus='Listeria',
            subspecies='',
        )
        self.client.login(
            username='geneseekr-workflow-test',
            password='password',
        )

    def test_download_container_skips_directory_marker_objects(self):
        storage = mock.Mock()
        storage.list_objects.return_value = [
            SimpleNamespace(name='sequences/isolate'),
            SimpleNamespace(name='sequences/isolate/input.fasta'),
            SimpleNamespace(name='sequences/reports/amr_summary.csv'),
            SimpleNamespace(name='empty.txt'),
            SimpleNamespace(name='empty-folder/'),
        ]

        with tempfile.TemporaryDirectory() as output_dir:
            download_container(storage, 'amrsummary-test', output_dir)

        self.assertEqual(
            [call.args[1] for call in storage.download_file.call_args_list],
            [
                'sequences/isolate/input.fasta',
                'sequences/reports/amr_summary.csv',
                'empty.txt',
            ],
        )

    @mock.patch('olc_webportalv2.geneseekr.tasks.get_storage_client')
    def test_benchmark_download_uses_latest_version_and_read_sas(
        self, get_storage_client
    ):
        blob_client = get_storage_client.return_value
        blob_client.list_objects.return_value = [
            SimpleNamespace(name='bds-salmonella-1.0.0.zip', size=100),
            SimpleNamespace(name='bds-salmonella-1.10.0.zip', size=217),
        ]
        blob_client.create_transfer_url.return_value = 'benchmark-url'

        download = _create_benchmark_download(
            benchmark_name='bds-salmonella'
        )

        self.assertEqual(download['blob_name'], 'bds-salmonella-1.10.0.zip')
        self.assertEqual(download['benchmark_version'], '1.10.0')
        self.assertEqual(download['content_length'], 217)
        blob_client.list_objects.assert_called_once_with(
            'benchmark-datasets',
            prefix='bds-salmonella',
        )
        blob_client.create_transfer_url.assert_called_once_with(
            'benchmark-datasets',
            'read',
            name='bds-salmonella-1.10.0.zip',
            expires_in_seconds=12 * 60 * 60,
        )

    @mock.patch('olc_webportalv2.geneseekr.views.run_geneseekr')
    def test_query_creates_processing_request_and_queues_task(self, run_task):
        response = self.client.post(
            reverse('geneseekr:geneseekr_query'),
            {
                'seqids': '2015-SEQ-0711',
                'query_sequence': '>query\nACGT',
                'name': 'Workflow query',
            },
        )

        request = GeneSeekrRequest.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.status, 'Processing')
        self.assertEqual(request.query_sequence, '>query\nACGT')
        self.assertEqual(request.name, 'Workflow query')
        self.assertEqual(run_task.apply_async.call_count, 1)

    def test_processing_page_saves_new_email(self):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            query_sequence='>query\nACGT',
        )

        response = self.client.post(
            reverse(
                'geneseekr:geneseekr_processing',
                kwargs={'geneseekr_request_pk': request.pk},
            ),
            {'email': 'alerts@example.com'},
        )

        request.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.emails_array, ['alerts@example.com'])

    def test_processing_page_saves_email_when_email_array_is_null(self):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            emails_array=None,
        )

        response = self.client.post(
            reverse(
                'geneseekr:geneseekr_processing',
                kwargs={'geneseekr_request_pk': request.pk},
            ),
            {'email': 'alerts@example.com'},
        )

        request.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.emails_array, ['alerts@example.com'])

    @mock.patch('olc_webportalv2.geneseekr.views.id_sync', return_value={})
    def test_results_page_displays_gene_hits(self, id_sync):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            query_sequence='>query\nACGT',
            gene_targets=['geneA'],
        )
        hit = TopBlastHit.objects.create(
            geneseekr_request=request,
            contig_name='contig-1',
            query_coverage=90.0,
            percent_identity=95.0,
            start_position=1,
            end_position=4,
            e_value=0.001,
            gene_name='geneA',
        )

        response = self.client.get(
            reverse(
                'geneseekr:geneseekr_results',
                kwargs={'geneseekr_request_pk': request.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['gene_top_hits']['geneA']), [hit]
        )
        self.assertEqual(id_sync.call_count, 1)

    def test_submission_views_create_processing_requests(self):
        with mock.patch(
            'olc_webportalv2.geneseekr.views.run_mash'
        ) as run_mash:
            response = self.client.post(
                reverse('geneseekr:tree_request'),
                {'seqids': '2015-SEQ-0711 2015-SEQ-0712'},
            )
        tree = Tree.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(tree.status, 'Processing')
        self.assertEqual(run_mash.apply_async.call_count, 1)

        with mock.patch(
            'olc_webportalv2.geneseekr.views.run_amr_summary'
        ) as run_amr_summary:
            response = self.client.post(
                reverse('geneseekr:amr_request'),
                {'seqids': '2015-SEQ-0711'},
            )
        amr_request = AMRSummary.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(amr_request.status, 'Processing')
        self.assertEqual(run_amr_summary.apply_async.call_count, 1)

        with (
            mock.patch('olc_webportalv2.geneseekr.views.get_storage_client'),
            mock.patch(
                'olc_webportalv2.geneseekr.views.run_prokka'
            ) as run_prokka,
        ):
            response = self.client.post(
                reverse('geneseekr:prokka_request'),
                {'seqids': '2015-SEQ-0711'},
            )
        prokka_request = ProkkaRequest.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(prokka_request.status, 'Processing')
        self.assertEqual(run_prokka.apply_async.call_count, 1)

        with mock.patch(
            'olc_webportalv2.geneseekr.views.run_nearest_neighbors'
        ) as run_neighbors:
            response = self.client.post(
                reverse('geneseekr:neighbor_request'),
                {'seqid': '2015-SEQ-0711', 'number_neighbors': 10},
            )
        neighbor_request = NearestNeighbors.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(neighbor_request.status, 'Processing')
        self.assertEqual(run_neighbors.apply_async.call_count, 1)

    @mock.patch('olc_webportalv2.geneseekr.views.get_storage_client')
    @mock.patch('olc_webportalv2.geneseekr.views.run_mash')
    def test_tree_upload_is_staged_in_sequences_directory(
        self, run_mash, get_storage_client
    ):
        upload = SimpleUploadedFile('extra.fasta', b'>extra\nACGT\n')

        response = self.client.post(
            reverse('geneseekr:tree_request'),
            {
                'seqids': '2015-SEQ-0711 2015-SEQ-0712',
                'other_files': upload,
            },
        )

        tree = Tree.objects.get(user=self.user)
        storage = get_storage_client.return_value
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            tree.other_input_files,
            [f'tree-{tree.pk}/sequences/extra.fasta'],
        )
        storage.upload_bytes.assert_called_once_with(
            f'tree-{tree.pk}',
            'sequences/extra.fasta',
            b'>extra\nACGT\n',
            content_type='text/plain',
        )
        self.assertEqual(run_mash.apply_async.call_count, 1)

    @mock.patch('olc_webportalv2.geneseekr.views.get_storage_client')
    @mock.patch('olc_webportalv2.geneseekr.views.run_amr_summary')
    def test_amr_upload_is_staged_in_sequences_directory(
        self, run_amr_summary, get_storage_client
    ):
        upload = SimpleUploadedFile('isolate.fasta', b'>isolate\nACGT\n')

        response = self.client.post(
            reverse('geneseekr:amr_request'),
            {'other_files': upload},
        )

        request = AMRSummary.objects.get(user=self.user)
        storage = get_storage_client.return_value
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            request.other_input_files,
            [f'amrsummary-{request.pk}/sequences/isolate.fasta'],
        )
        storage.upload_bytes.assert_called_once_with(
            f'amrsummary-{request.pk}',
            'sequences/isolate.fasta',
            b'>isolate\nACGT\n',
            content_type='text/plain',
        )
        self.assertEqual(run_amr_summary.apply_async.call_count, 1)

    @mock.patch('olc_webportalv2.geneseekr.tasks.generic_api_submit')
    def test_amr_submission_stages_inputs_and_uses_shared_database(
        self, generic_api_submit
    ):
        request = AMRSummary.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711'],
        )

        run_amr_summary(request.pk)

        submission = generic_api_submit.call_args.kwargs
        self.assertEqual(
            submission['input_file_pattern'],
            [['processed-data/2015-SEQ-0711.fasta', 'sequences']],
        )
        self.assertIn(
            '-t /databases/0.5.0.23/resfinder',
            submission['command'],
        )
        self.assertIn('-r /databases/0.5.0.23', submission['command'])
        self.assertNotIn('/datadrive/0.5.0.23', submission['command'])
        self.assertNotIn('mv $AZ_BATCH_NODE_MOUNTS_DIR', submission['command'])

    @mock.patch(
        'olc_webportalv2.geneseekr.tasks.os.path.isdir',
        return_value=True,
    )
    @mock.patch('olc_webportalv2.geneseekr.tasks.generic_api_submit')
    def test_tree_submission_writes_tree_where_finalizer_expects_it(
        self, generic_api_submit, isdir
    ):
        request = Tree.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711', '2015-SEQ-0712'],
        )

        run_mash(request.pk)

        submission = generic_api_submit.call_args.kwargs
        self.assertEqual(
            submission['input_file_pattern'],
            [
                ['processed-data/2015-SEQ-0711.fasta', 'sequences'],
                ['processed-data/2015-SEQ-0712.fasta', 'sequences'],
            ],
        )
        self.assertIn(
            f'$AZ_BATCH_NODE_MOUNTS_DIR/tree-{request.pk}/mash.tree',
            submission['command'],
        )
        self.assertIn('/envs/cowbat', submission['command'])
        self.assertIn(
            'mash triangle -p 4 -s 10000 -k 21',
            submission['command'],
        )
        self.assertIn('DistanceTreeConstructor().nj', submission['command'])
        self.assertNotIn(
            f'tree-{request.pk}/tree-{request.pk}/mash.tree',
            submission['command'],
        )

    @mock.patch('olc_webportalv2.cowbat.tasks.generate_download_link')
    @mock.patch('olc_webportalv2.cowbat.tasks.upload_blob_from_path')
    @mock.patch('olc_webportalv2.cowbat.tasks.get_storage_client')
    @mock.patch('olc_webportalv2.cowbat.tasks.download_container')
    @mock.patch('olc_webportalv2.cowbat.tasks.create_batch_client')
    def test_amr_finalizer_imports_nested_report_and_uses_supported_link_ttl(
        self,
        create_batch_client,
        download_container,
        get_storage_client,
        upload_blob_from_path,
        generate_download_link,
    ):
        request = AMRSummary.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711'],
            status='Processing',
        )
        tracker = AMRAzureRequest.objects.create(amr_request=request)
        batch_client = create_batch_client.return_value
        batch_client.task.list.return_value = [
            SimpleNamespace(
                id=f'amrsummary-{request.pk}-0',
                state='completed',
                execution_info=SimpleNamespace(exit_code=0),
            )
        ]
        generate_download_link.return_value = 'download-url'

        def write_report(
            *, blob_service_client, container_name, output_dir
        ):
            report_dir = os.path.join(
                output_dir,
                container_name,
                'sequences',
                'reports',
            )
            os.makedirs(report_dir)
            with open(
                os.path.join(report_dir, 'amr_summary.csv'),
                'w',
                encoding='utf-8',
            ) as report:
                report.write(
                    'Strain,Gene,Location\n'
                    '2015-SEQ-0711,blaZ,contig1:1-9\n'
                )

        original_directory = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.chdir(temp_dir)
                download_container.side_effect = write_report
                check_amr_summary_tasks()
        finally:
            os.chdir(original_directory)

        request.refresh_from_db()
        self.assertEqual(request.status, 'Complete')
        self.assertEqual(request.download_link, 'download-url')
        self.assertEqual(
            request.amrdetail.get().amr_results,
            {'blaZ': 'contig1:1-9'},
        )
        self.assertFalse(
            AMRAzureRequest.objects.filter(pk=tracker.pk).exists()
        )
        generate_download_link.assert_called_once_with(
            blob_service_client=get_storage_client.return_value,
            container_name=f'amrsummary-{request.pk}',
            blob_name=f'amrsummary-{request.pk}.zip',
            expiry=7,
        )

    @mock.patch('olc_webportalv2.cowbat.tasks.create_batch_client')
    def test_tree_missing_job_does_not_downgrade_completed_request(
        self, create_batch_client
    ):
        request = Tree.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711'],
            status='Processing',
        )
        tracker = TreeAzureRequest.objects.create(tree_request=request)
        batch_client = create_batch_client.return_value

        def finish_then_remove_job(job_id):
            Tree.objects.filter(pk=request.pk).update(status='Complete')
            raise BatchServiceError(404, 'job no longer exists')

        batch_client.task.list.side_effect = finish_then_remove_job
        check_tree_tasks()

        request.refresh_from_db()
        self.assertEqual(request.status, 'Complete')
        self.assertFalse(
            TreeAzureRequest.objects.filter(pk=tracker.pk).exists()
        )

    @mock.patch('olc_webportalv2.cowbat.tasks.create_batch_client')
    def test_amr_missing_job_does_not_downgrade_completed_request(
        self, create_batch_client
    ):
        request = AMRSummary.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711'],
            status='Processing',
        )
        tracker = AMRAzureRequest.objects.create(amr_request=request)
        batch_client = create_batch_client.return_value

        def finish_then_remove_job(job_id):
            AMRSummary.objects.filter(pk=request.pk).update(
                status='Complete'
            )
            raise BatchServiceError(404, 'job no longer exists')

        batch_client.task.list.side_effect = finish_then_remove_job
        check_amr_summary_tasks()

        request.refresh_from_db()
        self.assertEqual(request.status, 'Complete')
        self.assertFalse(
            AMRAzureRequest.objects.filter(pk=tracker.pk).exists()
        )

    @mock.patch('olc_webportalv2.cowbat.tasks.generate_download_link')
    @mock.patch('olc_webportalv2.cowbat.tasks.upload_blob_from_path')
    @mock.patch('olc_webportalv2.cowbat.tasks.get_storage_client')
    @mock.patch('olc_webportalv2.cowbat.tasks.download_container')
    @mock.patch('olc_webportalv2.cowbat.tasks.create_batch_client')
    def test_tree_finalizer_reads_tree_from_container_root_and_uses_seven_days(
        self,
        create_batch_client,
        download_container,
        get_storage_client,
        upload_blob_from_path,
        generate_download_link,
    ):
        request = Tree.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711', '2015-SEQ-0712'],
            number_diversitree_strains=0,
            status='Processing',
        )
        tracker = TreeAzureRequest.objects.create(
            tree_request=request,
            exit_code_file='unused',
        )
        batch_client = create_batch_client.return_value
        batch_client.task.list.return_value = [
            SimpleNamespace(
                id=f'tree-{request.pk}-0',
                state='completed',
                execution_info=SimpleNamespace(exit_code=0),
            )
        ]
        generate_download_link.return_value = 'tree-download-url'

        def write_tree(*, blob_service_client, container_name, output_dir):
            tree_dir = os.path.join(output_dir, container_name)
            os.makedirs(tree_dir)
            with open(
                os.path.join(tree_dir, 'mash.tree'),
                'w',
                encoding='utf-8',
            ) as tree:
                tree.write('(2015-SEQ-0711:0.1,2015-SEQ-0712:0.2);\n')

        original_directory = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.chdir(temp_dir)
                download_container.side_effect = write_tree
                check_tree_tasks()
        finally:
            os.chdir(original_directory)

        request.refresh_from_db()
        self.assertEqual(request.status, 'Complete')
        self.assertEqual(request.download_link, 'tree-download-url')
        self.assertEqual(
            request.newick_tree,
            '(2015-SEQ-0711:0.1,2015-SEQ-0712:0.2);',
        )
        self.assertFalse(
            TreeAzureRequest.objects.filter(pk=tracker.pk).exists()
        )
        generate_download_link.assert_called_once_with(
            blob_service_client=get_storage_client.return_value,
            container_name=f'tree-{request.pk}',
            blob_name=f'tree-{request.pk}.zip',
            expiry=7,
        )

    @mock.patch('olc_webportalv2.cowbat.tasks.create_batch_client')
    def test_finalizers_mark_empty_batch_jobs_as_errors(
        self, create_batch_client
    ):
        amr_request = AMRSummary.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711'],
            status='Processing',
        )
        amr_tracker = AMRAzureRequest.objects.create(amr_request=amr_request)
        tree_request = Tree.objects.create(
            user=self.user,
            seqids=['2015-SEQ-0711'],
            status='Processing',
        )
        tree_tracker = TreeAzureRequest.objects.create(
            tree_request=tree_request,
            exit_code_file='unused',
        )
        batch_client = create_batch_client.return_value
        batch_client.task.list.return_value = []

        check_amr_summary_tasks()
        check_tree_tasks()

        amr_request.refresh_from_db()
        tree_request.refresh_from_db()
        self.assertEqual(amr_request.status, 'Error')
        self.assertEqual(tree_request.status, 'Error')
        self.assertFalse(
            AMRAzureRequest.objects.filter(pk=amr_tracker.pk).exists()
        )
        self.assertFalse(
            TreeAzureRequest.objects.filter(pk=tree_tracker.pk).exists()
        )
        self.assertEqual(batch_client.job.delete.call_count, 2)
        self.assertEqual(batch_client.pool.delete.call_count, 2)

    def test_other_users_cannot_view_results(self):
        other_user = User.objects.create_user(username='another-user')
        owned_requests = [
            (
                GeneSeekrRequest.objects.create(user=other_user),
                'geneseekr:geneseekr_results',
                'geneseekr_request_pk',
            ),
            (
                Tree.objects.create(user=other_user),
                'geneseekr:tree_result',
                'tree_request_pk',
            ),
            (
                AMRSummary.objects.create(user=other_user),
                'geneseekr:amr_result',
                'amr_request_pk',
            ),
            (
                ProkkaRequest.objects.create(user=other_user),
                'geneseekr:prokka_result',
                'prokka_request_pk',
            ),
            (
                NearestNeighbors.objects.create(
                    user=other_user, number_neighbors=2
                ),
                'geneseekr:neighbor_result',
                'neighbor_request_pk',
            ),
        ]

        for request, url_name, parameter in owned_requests:
            response = self.client.get(
                reverse(url_name, kwargs={parameter: request.pk})
            )
            self.assertEqual(response.status_code, 404, url_name)

    def test_amr_and_prokka_email_submissions_are_saved(self):
        amr_request = AMRSummary.objects.create(user=self.user)
        response = self.client.post(
            reverse(
                'geneseekr:amr_result',
                kwargs={'amr_request_pk': amr_request.pk},
            ),
            {'email': 'amr@example.com'},
        )
        self.assertEqual(response.status_code, 200)
        amr_request.refresh_from_db()
        self.assertEqual(amr_request.emails_array, ['amr@example.com'])

        prokka_request = ProkkaRequest.objects.create(user=self.user)
        response = self.client.post(
            reverse(
                'geneseekr:prokka_result',
                kwargs={'prokka_request_pk': prokka_request.pk},
            ),
            {'email': 'prokka@example.com'},
        )
        self.assertEqual(response.status_code, 200)
        prokka_request.refresh_from_db()
        self.assertEqual(prokka_request.emails_array, ['prokka@example.com'])

    def test_duplicate_email_is_not_added_twice(self):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            emails_array=['alerts@example.com'],
        )

        response = self.client.post(
            reverse(
                'geneseekr:geneseekr_processing',
                kwargs={'geneseekr_request_pk': request.pk},
            ),
            {'email': 'alerts@example.com'},
        )

        request.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.emails_array, ['alerts@example.com'])

    def test_invalid_email_is_not_saved(self):
        request = GeneSeekrRequest.objects.create(user=self.user)

        response = self.client.post(
            reverse(
                'geneseekr:geneseekr_processing',
                kwargs={'geneseekr_request_pk': request.pk},
            ),
            {'email': 'not-an-email'},
        )

        request.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.emails_array, [])

    def test_home_delete_does_not_delete_another_users_request(self):
        other_user = User.objects.create_user(username='delete-target')
        request = GeneSeekrRequest.objects.create(user=other_user)

        response = self.client.post(
            reverse('geneseekr:geneseekr_home'),
            {'delete': request.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            GeneSeekrRequest.objects.filter(pk=request.pk).exists()
        )
