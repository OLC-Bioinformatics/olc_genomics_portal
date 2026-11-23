import json
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

from django.test import TestCase

from olc_webportalv2.common.batch_client import BatchServiceError
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorAnalysisTask,
    ValidatorRequest,
    ValidatorSample,
    ValidatorTarget,
    VerifierAzureRequest,
    VerifierAnalysisTask,
)
from olc_webportalv2.primer_finder.tasks import (
    _create_validator_gene_task_command,
    _normalize_gene_reports,
    _prepare_validator_analysis,
    _process_validator_analysis_task,
    _process_verifier_task,
    _process_verifier_analysis_task,
    _process_primer_request,
    _submit_verifier_task,
    run_primer_verifier,
    _submit_validator_task,
    _upload_gene_category_report,
    _upload_validator_gene_targets,
    post_validator_run,
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

    def test_gene_category_exports_include_inputs_and_decision_evidence(self):
        self.request.gene_results = [
            {'identity': '95', 'query': 'high'},
            {'identity': '80', 'query': 'low'},
        ]
        self.request.gene_exclusion_categories = ['FRAME_SHIFT']
        self.request.gene_exclusion_results = {'isolate': ['FRAME_SHIFT']}
        self.request.gene_exclusion_details = {
            'isolate': {'invA': [{
                'category': 'FRAME_SHIFT',
                'reason': 'out of frame',
                'hit': {'query': 'high', 'identity': '95'},
            }]},
        }
        storage = Mock()

        _upload_gene_category_report(
            blob_client=storage,
            container_name='primer-validator-1',
            request=self.request,
            gene_reports={'hits.json': 'raw data'},
        )

        uploads = {call.args[1]: call.args[2] for call in storage.upload_bytes.call_args_list}
        payload = json.loads(uploads['reports/gene_category_evaluation.json'])
        self.assertEqual(payload['summary']['eligible_hits'], 1)
        self.assertEqual(payload['summary']['below_identity_cutoff_hits'], 1)
        self.assertEqual(payload['selected_categories'], ['FRAME_SHIFT'])
        self.assertEqual(payload['gene_reports'], {'hits.json': 'raw data'})
        tsv = uploads['reports/gene_category_evaluation.tsv'].decode()
        self.assertIn('isolate\tinvA\tFRAME_SHIFT\tout of frame\thigh\t95', tsv)

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

    def test_post_run_saves_filtered_report_and_publishes_download_links(self):
        self.request.report = json.dumps({"original": "report"})
        self.request.save(update_fields=["report"])

        with tempfile.TemporaryDirectory() as directory:
            gene_folder = Path(directory) / "gene"
            gene_folder.mkdir()
            gene_report = gene_folder / "hits.json"
            gene_report.write_text(
                json.dumps(
                    {
                        "query": "sample__invA",
                        "target": "invA",
                        "identity": "99.5",
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch(
                    "olc_webportalv2.primer_finder.tasks.get_storage_client"
                ) as get_storage,
                patch(
                    "olc_webportalv2.primer_finder.tasks."
                    "_cleanup_blob_container"
                ) as cleanup,
                patch(
                    "olc_webportalv2.primer_finder.tasks.populate_report",
                    return_value=(self.request, directory),
                ),
                patch(
                    "olc_webportalv2.primer_finder.tasks."
                    "categorize_gene_hits",
                    return_value={"category": ["invA"]},
                ) as categorize,
                patch(
                    "olc_webportalv2.primer_finder.tasks."
                    "categorize_gene_hit_details",
                    return_value={"category": {"invA": [{
                        "category": "FRAME_SHIFT",
                        "hit": {"query": "sample__invA"},
                    }]}},
                ),
                patch(
                    "olc_webportalv2.primer_finder.tasks."
                    "gene_exclusion_note",
                    return_value="No exclusions applied.",
                ),
                patch(
                    "olc_webportalv2.primer_finder.tasks."
                    "filter_report_by_gene_categories",
                    return_value='{"filtered": true}',
                ) as filter_report,
                patch(
                    "olc_webportalv2.primer_finder.tasks.create_details",
                    return_value=self.request,
                ) as create_details,
                patch(
                    "olc_webportalv2.primer_finder.tasks."
                    "_upload_summary_excel"
                ) as upload_summary,
                patch(
                    "olc_webportalv2.primer_finder.tasks."
                    "_archive_reports_and_upload"
                ) as archive_reports,
                patch(
                    "olc_webportalv2.primer_finder.tasks._generate_sas_urls",
                    return_value=("excel-url", "archive-url"),
                ) as generate_urls,
            ):
                result = post_validator_run(
                    batch_job_name=self.request.container_namer(),
                    validator_request=self.request,
                )

        self.request.refresh_from_db()
        self.assertEqual(result, ("archive-url", "excel-url", directory))
        self.assertEqual(self.request.status, "Complete")
        self.assertEqual(self.request.report, '{"filtered": true}')
        self.assertEqual(self.request.report_download_link, "archive-url")
        self.assertEqual(self.request.summary_download_link, "excel-url")
        self.assertEqual(
            json.loads(self.request.validator_original_report),
            {"original": "report"},
        )
        self.assertEqual(
            self.request.gene_results[0]["query"],
            "sample__invA",
        )
        cleanup.assert_called_once()
        categorize.assert_called_once()
        filter_report.assert_called_once()
        create_details.assert_called_once()
        upload_summary.assert_called_once()
        archive_reports.assert_called_once()
        generate_urls.assert_called_once()
        get_storage.assert_called_once()


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

    def batch_client(self, *, state, exit_code=0, prefix='primer-verifier'):
        cloud_task = SimpleNamespace(
            state=state,
            execution_info=SimpleNamespace(exit_code=exit_code),
        )
        def list_tasks(job_id):
            if job_id == f'{prefix}-{self.request.pk}':
                return [cloud_task]
            raise BatchServiceError(404, 'not found')

        return SimpleNamespace(
            task=SimpleNamespace(list=Mock(side_effect=list_tasks)),
            job=SimpleNamespace(delete=Mock()),
            pool=SimpleNamespace(delete=Mock(), exists=Mock(return_value=True)),
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

        batch_job_name = f'primer-verifier-{self.request.pk}'
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

    def test_legacy_dev_job_uses_its_original_container(self):
        batch_client = self.batch_client(state='completed', prefix='primer-validator')
        with patch(
            'olc_webportalv2.primer_finder.tasks._handle_successful_verifier_run'
        ) as finalize:
            _process_verifier_task(batch_client=batch_client, task=self.task)
        finalize.assert_called_once_with(
            batch_job_name=f'primer-validator-{self.request.pk}',
            verifier_request=self.request,
        )

    def test_ambiguous_legacy_jobs_are_not_finalized_or_deleted(self):
        batch_client = self.batch_client(state='completed')
        batch_client.task.list.side_effect = None
        batch_client.task.list.return_value = [
            SimpleNamespace(state='completed', execution_info=SimpleNamespace(exit_code=0))
        ]
        _process_verifier_task(batch_client=batch_client, task=self.task)
        self.assertTrue(VerifierAzureRequest.objects.filter(pk=self.task.pk).exists())
        batch_client.job.delete.assert_not_called()

    def test_verifier_submits_both_jobs_with_staged_targets(self):
        manifest = [
            {'blob_name': 'primers.fasta', 'sas_url': 'https://storage/primers'},
            {'blob_name': 'inclusivity/ncbi-large.zip', 'sas_url': 'https://storage/panel'},
        ]
        with (
            patch('olc_webportalv2.primer_finder.tasks.upload_primers',
                  return_value=(self.request.container_namer(), 'primers.fasta')),
            patch('olc_webportalv2.primer_finder.tasks._handle_panel_files',
                  side_effect=[['inclusivity/ncbi-large.zip'], []]),
            patch('olc_webportalv2.primer_finder.tasks._generate_input_blob_sas_urls',
                  return_value=manifest),
            patch('olc_webportalv2.primer_finder.tasks._submit_verifier_task') as submit,
        ):
            _process_primer_request(
                request_model=PrimerVerifierRequest, request_pk=self.request.pk
            )
        self.assertEqual(self.request.analysis_tasks.count(), 2)
        self.assertEqual(submit.call_count, 2)
        primer, gene = [call.kwargs for call in submit.call_args_list]
        self.assertEqual(primer['vm_size'], 'Standard_D16ds_v5')
        self.assertEqual(gene['vm_size'], 'Standard_D16ds_v5')
        self.assertIn('targets/query.fasta', gene['command'])
        self.assertIn('*.fasta|*.fa|*.fna|*.fas', gene['command'])

    def test_focused_jobs_finalize_only_after_both_finish(self):
        tasks = [
            VerifierAnalysisTask.objects.create(
                verifier_request=self.request, task_type=kind, status='submitted',
                batch_job_id=f'{self.request.container_namer()}-{kind}-job',
            )
            for kind in ('primer', 'gene')
        ]
        batch_client = SimpleNamespace(task=SimpleNamespace(list=Mock(return_value=[
            SimpleNamespace(state='completed', execution_info=SimpleNamespace(exit_code=0))
        ])))
        with (
            patch('olc_webportalv2.primer_finder.tasks._handle_successful_verifier_run') as finalize,
            patch('olc_webportalv2.primer_finder.tasks.retry_failed_pool_resize',
                  return_value={'action': 'none'}),
            patch('olc_webportalv2.primer_finder.tasks._delete_validator_batch_resources',
                  return_value=True),
        ):
            _process_verifier_analysis_task(batch_client=batch_client, task=tasks[0])
            finalize.assert_not_called()
            _process_verifier_analysis_task(batch_client=batch_client, task=tasks[1])
        finalize.assert_called_once_with(
            batch_job_name=self.request.container_namer(),
            verifier_request=self.request,
        )

    def test_focused_submission_failure_is_recorded(self):
        task = VerifierAnalysisTask.objects.create(
            verifier_request=self.request, task_type='gene'
        )
        with patch(
            'olc_webportalv2.primer_finder.tasks.generic_api_submit',
            return_value={'status': 'Error'},
        ):
            with self.assertRaisesRegex(RuntimeError, 'submission failed'):
                _submit_verifier_task(
                    request=self.request, analysis_task=task,
                    command='gene-command', vm_size='Standard_D8s_v3',
                )
        task.refresh_from_db()
        self.assertEqual(task.status, 'error')
        self.assertIn('submission failed', task.error)

    def test_inflight_production_jobs_finalize_in_original_container(self):
        prefix = f'primer-verifier-{self.request.pk}'
        VerifierAnalysisTask.objects.create(
            verifier_request=self.request, task_type='primer', status='complete'
        )
        gene = VerifierAnalysisTask.objects.create(
            verifier_request=self.request, task_type='gene', status='submitted',
            batch_job_id=f'{prefix}-gene-job',
        )
        batch_client = SimpleNamespace(task=SimpleNamespace(list=Mock(return_value=[
            SimpleNamespace(state='completed', execution_info=SimpleNamespace(exit_code=0))
        ])))
        with (
            patch('olc_webportalv2.primer_finder.tasks._handle_successful_verifier_run') as finalize,
            patch('olc_webportalv2.primer_finder.tasks.retry_failed_pool_resize',
                  return_value={'action': 'none'}),
            patch('olc_webportalv2.primer_finder.tasks._delete_validator_batch_resources',
                  return_value=True),
        ):
            _process_verifier_analysis_task(batch_client=batch_client, task=gene)
        finalize.assert_called_once_with(
            batch_job_name=prefix, verifier_request=self.request
        )

    def test_resize_retry_keeps_focused_task_submitted(self):
        task = VerifierAnalysisTask.objects.create(
            verifier_request=self.request, task_type='primer', status='submitted',
            batch_job_id=f'{self.request.container_namer()}-primer-job',
        )
        client = SimpleNamespace(task=SimpleNamespace(list=Mock()))
        with patch(
            'olc_webportalv2.primer_finder.tasks.retry_failed_pool_resize',
            return_value={'action': 'resize_retried'},
        ):
            _process_verifier_analysis_task(batch_client=client, task=task)
        task.refresh_from_db()
        self.assertEqual(task.status, 'submitted')
        client.task.list.assert_not_called()

    def test_partial_submission_preserves_first_job_for_cleanup(self):
        prefix = self.request.container_namer()
        manifest = [{'blob_name': 'primers.fasta', 'sas_url': 'https://storage/p'}]
        with (
            patch('olc_webportalv2.primer_finder.tasks.upload_primers',
                  return_value=(prefix, 'primers.fasta')),
            patch('olc_webportalv2.primer_finder.tasks._handle_panel_files',
                  return_value=[]),
            patch('olc_webportalv2.primer_finder.tasks._generate_input_blob_sas_urls',
                  return_value=manifest),
            patch('olc_webportalv2.primer_finder.tasks.generic_api_submit',
                  side_effect=[
                      {'status': 'Success', 'job_id': f'{prefix}-primer-job',
                       'tasks': ['primer-task']},
                      {'status': 'Error'},
                  ]),
        ):
            run_primer_verifier(verifier_request_pk=self.request.pk)
        primer = self.request.analysis_tasks.get(task_type='primer')
        gene = self.request.analysis_tasks.get(task_type='gene')
        self.request.refresh_from_db()
        self.assertEqual((primer.status, primer.batch_job_id),
                         ('submitted', f'{prefix}-primer-job'))
        self.assertEqual(gene.status, 'error')
        self.assertEqual(self.request.status, 'Error')

    def test_legacy_job_with_validator_container_collision_is_not_deleted(self):
        ValidatorRequest.objects.create(pk=self.request.pk, user=self.request.user)
        batch_client = self.batch_client(state='completed')
        _process_verifier_task(batch_client=batch_client, task=self.task)
        self.assertTrue(VerifierAzureRequest.objects.filter(pk=self.task.pk).exists())
        batch_client.job.delete.assert_not_called()
