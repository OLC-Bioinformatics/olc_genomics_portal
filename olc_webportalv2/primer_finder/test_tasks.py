import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase

from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorAnalysisTask,
    ValidatorRequest,
    ValidatorSample,
    ValidatorTarget,
    VerifierAzureRequest,
)
from olc_webportalv2.primer_finder.tasks import (
    _create_validator_gene_task_command,
    _normalize_gene_reports,
    _prepare_validator_analysis,
    _process_validator_analysis_task,
    _process_verifier_task,
    _submit_validator_task,
    _upload_validator_gene_targets,
)
from olc_webportalv2.users.models import User


class PrimerValidatorTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='primer-task-user')
        self.request = ValidatorRequest.objects.create(
            user=self.user,
            project_name='task-test',
            forward_primer='ACGT',
            reverse_primer='TGCA',
        )

    def add_target(self, *, name='invA', gene_sequence='', **kwargs):
        sample = ValidatorSample.objects.create(
            validator_request=self.request,
            name='sample',
            logic='',
            classification_rule='none',
        )
        return ValidatorTarget.objects.create(
            sample=sample,
            name=name,
            forward_primer='ACGT',
            reverse_primer='TGCA',
            gene_sequence=gene_sequence,
            **kwargs,
        )

    def test_gene_targets_are_normalized_and_duplicate_fasta_ids_are_unique(
        self,
    ):
        self.add_target(
            gene_sequence='>reference description\nac gt\n>reference\nNNnn\n'
        )
        storage = Mock()

        with patch(
            'olc_webportalv2.primer_finder.tasks.get_storage_client',
            return_value=storage,
        ):
            uploaded = _upload_validator_gene_targets(
                container_name='primer-validator-1',
                request=self.request,
            )

        self.assertTrue(uploaded)
        storage.ensure_container.assert_called_once_with('primer-validator-1')
        storage.upload_bytes.assert_called_once_with(
            'primer-validator-1',
            'targets/query.fasta',
            (
                '>sample__invA__reference\nACGT\n'
                '>sample__invA__reference__2\nNNNN\n'
            ).encode('utf-8'),
        )

    def test_gene_target_upload_rejects_invalid_bases_before_storage(self):
        self.add_target(gene_sequence='ACGTX')
        storage = Mock()

        with patch(
            'olc_webportalv2.primer_finder.tasks.get_storage_client',
            return_value=storage,
        ):
            with self.assertRaisesRegex(ValueError, 'invalid bases: X'):
                _upload_validator_gene_targets(
                    container_name='primer-validator-1',
                    request=self.request,
                )

        storage.ensure_container.assert_not_called()
        storage.upload_bytes.assert_not_called()

    def test_gene_report_normalizes_json_and_tabular_column_variants(self):
        reports = _normalize_gene_reports(
            {
                'reports/hits.json': json.dumps(
                    {
                        'Query': 'sample__invA',
                        'Gene': 'invA',
                        'Identity': '99.5',
                        'Coverage': '100',
                        'qstart': '1',
                        'qend': '100',
                    }
                ),
                'reports/other.tsv': (
                    'query_id\tsubject_id\tpercent_identity\t'
                    'percent_coverage\n'
                    'sample__stn\tstn\t98\t95\n'
                ),
                'reports/broken.json': '{invalid',
            }
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(
            (
                reports[0]['query'],
                reports[0]['target'],
                reports[0]['identity'],
            ),
            ('sample__invA', 'invA', '99.5'),
        )
        self.assertEqual(reports[0]['query_start'], '1')
        self.assertEqual(
            (
                reports[1]['query'],
                reports[1]['target'],
                reports[1]['coverage'],
            ),
            ('sample__stn', 'stn', '95'),
        )

    def test_gene_command_quotes_input_urls_and_stages_reports(self):
        command = _create_validator_gene_task_command(
            request=self.request,
            container_name='primer-validator-1',
            input_blobs=[
                {
                    'blob_name': 'targets/query.fasta',
                    'sas_url': 'https://storage.example/query?sig=abc&sp=r',
                }
            ],
        )

        self.assertIn(
            "'https://storage.example/query?sig=abc&sp=r'",
            command,
        )
        self.assertIn('GeneSeekr blastn', command)
        self.assertIn(
            'mkdir -p $AZ_BATCH_NODE_MOUNTS_DIR/'
            'primer-validator-1/reports/gene',
            command,
        )

    def test_analysis_preparation_creates_primer_and_gene_tasks(self):
        manifest = [
            {
                'blob_name': 'primers.fasta',
                'sas_url': 'https://storage/primers',
            },
            {
                'blob_name': 'targets/query.fasta',
                'sas_url': 'https://storage/targets',
            },
        ]
        with (
            patch(
                'olc_webportalv2.primer_finder.tasks.upload_primers',
                return_value=('primer-run', 'primers.fasta'),
            ),
            patch(
                'olc_webportalv2.primer_finder.tasks._validator_probe_records',
                return_value=[],
            ),
            patch(
                'olc_webportalv2.primer_finder.tasks._handle_panel_files',
                side_effect=[['inclusivity/listeria.zip'], []],
            ),
            patch(
                'olc_webportalv2.primer_finder.tasks.'
                '_upload_validator_gene_targets',
                return_value=True,
            ),
            patch(
                'olc_webportalv2.primer_finder.tasks.'
                '_generate_input_blob_sas_urls',
                return_value=manifest,
            ) as generate_urls,
            patch(
                'olc_webportalv2.primer_finder.tasks._submit_validator_task'
            ) as submit,
        ):
            _prepare_validator_analysis(validator_request_pk=self.request.pk)

        tasks = {
            task.task_type: task
            for task in ValidatorAnalysisTask.objects.filter(
                validator_request=self.request
            )
        }
        self.assertEqual(set(tasks), {'primer', 'gene'})
        self.assertEqual(tasks['primer'].input_manifest, manifest)
        self.assertEqual(tasks['gene'].input_manifest, manifest)
        self.assertEqual(
            generate_urls.call_args.kwargs['blob_names'],
            [
                'primers.fasta',
                'inclusivity/listeria.zip',
                'targets/query.fasta',
            ],
        )
        self.assertEqual(submit.call_count, 2)
        submitted_types = {
            call.kwargs['analysis_task'].task_type
            for call in submit.call_args_list
        }
        self.assertEqual(submitted_types, {'primer', 'gene'})

    def test_batch_submission_records_ids_and_errors(self):
        analysis_task = ValidatorAnalysisTask.objects.create(
            validator_request=self.request,
            task_type='primer',
        )
        with patch(
            'olc_webportalv2.primer_finder.tasks.generic_api_submit',
            return_value={
                'status': 'Success',
                'job_id': 'job-1',
                'tasks': ['job-1-0'],
            },
        ) as submit:
            _submit_validator_task(
                request=self.request,
                analysis_task=analysis_task,
                command='echo run',
            )

        analysis_task.refresh_from_db()
        self.assertEqual(analysis_task.status, 'submitted')
        self.assertEqual(analysis_task.batch_job_id, 'job-1')
        self.assertEqual(analysis_task.batch_task_id, 'job-1-0')
        submit.assert_called_once()

        failed_task = ValidatorAnalysisTask.objects.create(
            validator_request=self.request,
            task_type='gene',
        )
        with patch(
            'olc_webportalv2.primer_finder.tasks.generic_api_submit',
            return_value={'status': 'Error', 'error': 'quota'},
        ):
            with self.assertRaisesRegex(RuntimeError, 'submission failed'):
                _submit_validator_task(
                    request=self.request,
                    analysis_task=failed_task,
                    command='echo run',
                )

        failed_task.refresh_from_db()
        self.assertEqual(failed_task.status, 'error')
        self.assertIn('quota', failed_task.error)

    def test_polling_waits_for_completion_before_finalizing(self):
        analysis_task = ValidatorAnalysisTask.objects.create(
            validator_request=self.request,
            task_type='primer',
            status='submitted',
            batch_job_id='primer-validator-1-primer',
        )
        batch_client = SimpleNamespace(
            task=SimpleNamespace(
                list=Mock(
                    return_value=[
                        SimpleNamespace(state='running'),
                    ]
                )
            )
        )

        with patch(
            'olc_webportalv2.primer_finder.tasks.'
            '_handle_successful_validator_run'
        ) as finalize:
            _process_validator_analysis_task(
                batch_client=batch_client,
                task=analysis_task,
            )

        analysis_task.refresh_from_db()
        self.assertEqual(analysis_task.status, 'submitted')
        finalize.assert_not_called()

    def test_completed_successful_task_finalizes_and_cleans_batch_resources(
        self,
    ):
        analysis_task = ValidatorAnalysisTask.objects.create(
            validator_request=self.request,
            task_type='primer',
            status='submitted',
            batch_job_id='primer-validator-1-primer',
            batch_task_id='task-0',
        )
        batch_client = SimpleNamespace(
            task=SimpleNamespace(
                list=Mock(
                    return_value=[
                        SimpleNamespace(
                            state='completed',
                            execution_info=SimpleNamespace(exit_code=0),
                        ),
                    ]
                )
            )
        )

        with (
            patch(
                'olc_webportalv2.primer_finder.tasks.'
                '_handle_successful_validator_run'
            ) as finalize,
            patch(
                'olc_webportalv2.primer_finder.tasks.'
                '_delete_validator_batch_resources',
                return_value=True,
            ) as cleanup,
        ):
            _process_validator_analysis_task(
                batch_client=batch_client,
                task=analysis_task,
            )

        analysis_task.refresh_from_db()
        self.assertEqual(analysis_task.status, 'complete')
        self.assertEqual(analysis_task.batch_job_id, '')
        self.assertEqual(analysis_task.batch_task_id, '')
        finalize.assert_called_once_with(
            batch_job_name=self.request.container_namer(),
            validator_request=self.request,
        )
        cleanup.assert_called_once_with(
            batch_client=batch_client,
            job_id='primer-validator-1-primer',
        )

    def test_failed_task_marks_request_error_and_sends_failure_to_finalizer(
        self,
    ):
        analysis_task = ValidatorAnalysisTask.objects.create(
            validator_request=self.request,
            task_type='primer',
            status='submitted',
            batch_job_id='primer-validator-1-primer',
        )
        batch_client = SimpleNamespace(
            task=SimpleNamespace(
                list=Mock(
                    return_value=[
                        SimpleNamespace(
                            state='completed',
                            execution_info=SimpleNamespace(exit_code=1),
                        ),
                    ]
                )
            )
        )

        with (
            patch(
                'olc_webportalv2.primer_finder.tasks.'
                '_handle_failed_validator_run'
            ) as finalize,
            patch(
                'olc_webportalv2.primer_finder.tasks.'
                '_delete_validator_batch_resources',
                return_value=True,
            ),
        ):
            _process_validator_analysis_task(
                batch_client=batch_client,
                task=analysis_task,
            )

        analysis_task.refresh_from_db()
        self.request.refresh_from_db()
        self.assertEqual(analysis_task.status, 'error')
        self.assertIn('exit codes: [1]', analysis_task.error)
        self.assertEqual(self.request.status, 'Error')
        self.assertEqual(self.request.errors, analysis_task.error)
        finalize.assert_called_once_with(validator_request=self.request)


class PrimerVerifierTaskTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='primer-verifier-task-user')
        self.request = PrimerVerifierRequest.objects.create(
            user=user,
            project_name='verifier-task-test',
            status='Processing',
        )
        self.task = VerifierAzureRequest.objects.create(
            verifier_request=self.request,
        )

    def batch_client(self, *, state, exit_code=0):
        cloud_task = SimpleNamespace(
            state=state,
            execution_info=SimpleNamespace(exit_code=exit_code),
        )
        return SimpleNamespace(
            task=SimpleNamespace(list=Mock(return_value=[cloud_task])),
            job=SimpleNamespace(delete=Mock()),
            pool=SimpleNamespace(delete=Mock()),
        )

    def test_running_batch_task_is_not_finalized_or_removed(self):
        batch_client = self.batch_client(state='running')

        with patch(
            'olc_webportalv2.primer_finder.tasks.'
            '_handle_successful_verifier_run'
        ) as finalize:
            _process_verifier_task(batch_client=batch_client, task=self.task)

        self.assertTrue(
            VerifierAzureRequest.objects.filter(pk=self.task.pk).exists()
        )
        batch_client.job.delete.assert_not_called()
        batch_client.pool.delete.assert_not_called()
        finalize.assert_not_called()

    def test_successful_batch_task_is_finalized_and_cleaned_up(self):
        batch_client = self.batch_client(state='completed')

        with patch(
            'olc_webportalv2.primer_finder.tasks.'
            '_handle_successful_verifier_run'
        ) as finalize:
            _process_verifier_task(batch_client=batch_client, task=self.task)

        batch_job_name = self.request.container_namer()
        batch_client.job.delete.assert_called_once_with(job_id=batch_job_name)
        batch_client.pool.delete.assert_called_once_with(
            pool_id=batch_job_name
        )
        finalize.assert_called_once_with(
            batch_job_name=batch_job_name,
            verifier_request=self.request,
        )
        self.assertFalse(
            VerifierAzureRequest.objects.filter(pk=self.task.pk).exists()
        )

    def test_failed_batch_task_marks_request_error_and_is_cleaned_up(self):
        batch_client = self.batch_client(state='completed', exit_code=1)

        _process_verifier_task(batch_client=batch_client, task=self.task)

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, 'Error')
        self.assertFalse(
            VerifierAzureRequest.objects.filter(pk=self.task.pk).exists()
        )
        batch_client.job.delete.assert_called_once()
        batch_client.pool.delete.assert_called_once()
