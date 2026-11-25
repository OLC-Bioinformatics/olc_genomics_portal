import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from olc_webportalv2.common.batch_client import BatchServiceError
from olc_webportalv2.cowbat.models import (
    NanoporeFile,
    NanoporeManifest,
    NanoporeProcessingBatch,
    NanoporeRun,
    SequencingRun,
)
from olc_webportalv2.cowbat.tasks import (
    _append_unique_run_errors,
    _batch_resource_missing,
    _batch_state_value,
    _batch_submission_is_retryable,
    _claim_nanopore_batch,
    _cleanup_nanopore_batch_resources,
    _nanopore_batch_is_active,
    _nanopore_collection_blocker,
    _nanopore_result_identity_error,
    _nanopore_result_payload,
    _reconcile_nanopore_generation_results,
    _scheduler_csvs,
    build_nanopore_command,
    check_nanopore_tasks,
    collect_nanopore_iteration,
    submit_nanopore_batch,
)


class NanoporeTaskHelperTests(TestCase):
    def test_batch_submission_retryability_uses_response_signals(self):
        response = SimpleNamespace(status_code=503)

        self.assertTrue(
            _batch_submission_is_retryable(
                response, {"error_code": "capacity_unavailable"}
            )
        )
        self.assertTrue(
            _batch_submission_is_retryable(
                SimpleNamespace(status_code=400),
                {"error": "Insufficient capacity for this VM size"},
            )
        )
        self.assertFalse(
            _batch_submission_is_retryable(
                SimpleNamespace(status_code=500), {"error": "unavailable"}
            )
        )

    def test_command_adds_manifest_and_control_paths_only_once(self):
        command = build_nanopore_command(
            "wrapper --run {run_id} --manifest-prefix existing",
            run_id=9,
            manifest_prefix="manifests/",
            control_blob="control/state.json",
        )

        self.assertEqual(command.count("--manifest-prefix"), 1)
        self.assertIn('--control-blob "control/state.json"', command)

    def test_batch_resource_and_state_values_are_normalized(self):
        missing = SimpleNamespace(
            error=SimpleNamespace(code=SimpleNamespace(value="JobNotFound")),
            response=None,
        )
        deleting = SimpleNamespace(state=SimpleNamespace(value="Pool-Deleting"))
        active = SimpleNamespace(state="Batch.PoolState.Running")

        self.assertTrue(_batch_resource_missing(missing))
        self.assertTrue(
            _batch_resource_missing(
                SimpleNamespace(status_code=404, response=None)
            )
        )
        self.assertFalse(
            _batch_resource_missing(
                SimpleNamespace(status_code=500, response=None)
            )
        )
        self.assertEqual(_batch_state_value(deleting), "pooldeleting")
        self.assertEqual(_batch_state_value(active), "running")

    def test_unique_run_errors_only_changes_new_messages(self):
        run = SimpleNamespace(errors=["existing"])

        self.assertTrue(
            _append_unique_run_errors(run, ["existing", "new", "new"])
        )
        self.assertEqual(run.errors, ["existing", "new"])
        self.assertFalse(_append_unique_run_errors(run, ["new"]))


class NanoporeDatabaseTaskTests(TestCase):
    def create_run(self, name="nanopore-test", workflow_state="processing"):
        run = SequencingRun.objects.create(
            run_name=name,
            workflow_state=workflow_state,
        )
        NanoporeRun.objects.create(sequencing_run=run)
        return run

    def create_batch(self, run, generation=1, status="pending", **kwargs):
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[],
            sha256="manifest-sha",
            generation=generation,
            finalized=True,
        )
        return NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            generation=generation,
            status=status,
            **kwargs,
        )

    def test_batch_queries_find_active_batch_and_collection_blocker(self):
        run = self.create_run()
        self.create_batch(run, status="pending")
        active = self.create_batch(
            run,
            generation=2,
            status="running",
            azure_job_id="azure-job",
        )

        self.assertTrue(_nanopore_batch_is_active(run))
        first_batch = run.nanopore_processing_batches.get(generation=1)
        self.assertEqual(_nanopore_collection_blocker(run), first_batch)

        first_batch.last_completed_at = timezone.now()
        first_batch.save(update_fields=["last_completed_at"])
        active.last_completed_at = timezone.now()
        active.save(update_fields=["last_completed_at"])
        self.assertIsNone(_nanopore_collection_blocker(run))

    def test_claim_assigns_uploaded_files_to_one_immutable_generation(self):
        run = self.create_run()
        files = [
            NanoporeFile.objects.create(
                sequencing_run=run,
                relative_path=f"pod5/{index}.pod5",
                blob_name=f"{run.run_name}/input/pod5/{index}.pod5",
                size_bytes=index,
                sha256=f"sha-{index}",
                status="uploaded",
            )
            for index in (1, 2)
        ]

        batch = _claim_nanopore_batch(run)

        self.assertEqual(batch.generation, 1)
        self.assertEqual(batch.pod5_count, 2)
        self.assertTrue(batch.manifest.finalized)
        self.assertEqual(len(batch.manifest.files), 2)
        self.assertEqual(
            set(
                NanoporeFile.objects.filter(
                    pk__in=[item.pk for item in files]
                ).values_list("processing_batch_id", flat=True)
            ),
            {batch.pk},
        )
        self.assertIsNone(_claim_nanopore_batch(run))

    @override_settings(
        NANOPORE_BARCODE_VALUES="12,16",
        NANOPORE_BARCODE_KIT="SQK-RBK114-24",
        NANOPORE_REFERENCE="references/panel.fasta",
    )
    def test_scheduler_csvs_persists_default_sample_rows(self):
        run = self.create_run()
        nanopore_run = run.nanopore_run

        input_csv, metadata_csv = _scheduler_csvs(run, nanopore_run)

        self.assertIn("references/panel.fasta", input_csv)
        self.assertIn("SQK-RBK114-24,12,16,nanopore-test", input_csv)
        self.assertEqual(
            metadata_csv,
            "Barcode,SEQID,OLNID\n"
            "12,barcode12,BARCODE-12\n"
            "16,barcode16,BARCODE-16\n",
        )
        self.assertEqual(nanopore_run.barcode_values, [12, 16])
        self.assertEqual(nanopore_run.barcode_kit, "SQK-RBK114-24")

    def test_result_payload_validates_json_object_and_identity(self):
        run = self.create_run()
        batch = self.create_batch(
            run, generation=3, status="submitted", report_url="result.json"
        )
        storage = mock.Mock()
        storage.object_exists.return_value = True
        payload = {
            "run_id": run.pk,
            "run_name": run.run_name,
            "generation": 3,
        }
        storage.download_bytes.return_value = json.dumps(payload).encode()

        self.assertEqual(_nanopore_result_payload(storage, batch), payload)
        self.assertIsNone(_nanopore_result_identity_error(run, batch, payload))
        self.assertIn(
            "identity mismatch",
            _nanopore_result_identity_error(
                run, batch, {**payload, "run_name": "different-run"}
            ),
        )
        storage.object_exists.return_value = False
        self.assertIsNone(_nanopore_result_payload(storage, batch))
        storage.object_exists.return_value = True
        storage.download_bytes.return_value = b"[]"
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            _nanopore_result_payload(storage, batch)

    def test_reconcile_persists_valid_completed_generation(self):
        run = self.create_run()
        batch = self.create_batch(
            run,
            generation=1,
            status="published",
            pod5_count=4,
            report_url="result.json",
        )
        storage = mock.Mock()
        storage.object_exists.return_value = True
        storage.download_bytes.return_value = json.dumps(
            {
                "run_id": run.pk,
                "run_name": run.run_name,
                "generation": batch.generation,
                "status": "succeeded",
                "scheduler_exit_code": "0",
            }
        ).encode()

        completed, failed = _reconcile_nanopore_generation_results(
            run, storage
        )

        batch.refresh_from_db()
        self.assertEqual([item.pk for item in completed], [batch.pk])
        self.assertEqual(failed, [])
        self.assertEqual(batch.status, "complete")
        self.assertEqual(batch.exit_code, 0)
        self.assertEqual(batch.processed_pod5_count, 4)
        self.assertIsNotNone(batch.last_completed_at)

    def test_collect_forced_iteration_claims_files_and_schedules_submission(
        self,
    ):
        run = self.create_run(workflow_state="upload_complete")
        run.nanopore_run.last_file_seen_at = None
        run.nanopore_run.save(update_fields=["last_file_seen_at"])
        NanoporeFile.objects.create(
            sequencing_run=run,
            relative_path="pod5/read.pod5",
            blob_name=f"{run.run_name}/input/pod5/read.pod5",
            size_bytes=10,
            status="uploaded",
        )

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            result = collect_nanopore_iteration.run(run.pk, force=True)

        run.refresh_from_db()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["pod5_count"], 1)
        self.assertEqual(run.workflow_state, "processing")
        self.assertEqual(len(callbacks), 1)

    def test_collection_defers_when_a_previous_generation_is_unpublished(self):
        run = self.create_run()
        blocker = self.create_batch(
            run, status="submitted", generation=1, azure_job_id="job-1"
        )
        NanoporeFile.objects.create(
            sequencing_run=run,
            relative_path="pod5/waiting.pod5",
            blob_name=f"{run.run_name}/input/pod5/waiting.pod5",
            size_bytes=10,
            status="uploaded",
        )

        result = collect_nanopore_iteration.run(run.pk, force=True)

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["active_batch_id"], blocker.pk)
        self.assertEqual(result["waiting_file_count"], 1)
        self.assertIsNone(blocker.last_completed_at)

    def test_collection_ignores_missing_or_closed_runs(self):
        missing_result = collect_nanopore_iteration.run(987654, force=True)
        closed_run = self.create_run(workflow_state="complete")
        closed_result = collect_nanopore_iteration.run(
            closed_run.pk, force=True
        )

        self.assertEqual(missing_result["reason"], "run does not exist")
        self.assertEqual(closed_result["reason"], "run is closed")

    @override_settings(
        NANOPORE_BATCH_COMMAND="wrapper --run {run_name}",
        NANOPORE_RAW_CONTAINER="nanopore-raw",
        NANOPORE_RESULTS_CONTAINER="nanopore-results",
        NANOPORE_BATCH_VM_SIZE="Standard_NC24ads_A100_v4",
        NANOPORE_REFERENCE="references/panel.fasta",
        NANOPORE_BARCODE_VALUES="12",
        NANOPORE_BARCODE_KIT="SQK-RBK114-24",
        NANOPORE_DORADO_MODEL="dorado-model",
    )
    def test_submit_publishes_inputs_and_records_batch_identifiers(self):
        run = self.create_run(workflow_state="upload_complete")
        batch = self.create_batch(run, status="pending", generation=1)
        batch.manifest.files = [
            {
                "blob_name": "nanopore-test/input/pod5/read.pod5",
                "relative_path": "pod5/read.pod5",
            }
        ]
        batch.manifest.save(update_fields=["files"])
        storage = mock.Mock()
        storage.create_transfer_url.side_effect = [
            "input-read-url",
            "output-write-url",
        ]

        with (
            mock.patch(
                "olc_webportalv2.cowbat.tasks.get_storage_client",
                return_value=storage,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks.generic_api_submit",
                return_value={
                    "status": "submitted",
                    "job_id": "job-1",
                    "tasks": ["task-1"],
                    "pool_id": "pool-1",
                },
            ) as submit,
        ):
            submit_nanopore_batch.run(run.pk)

        batch.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(batch.status, "submitted")
        self.assertEqual(batch.azure_job_id, "job-1")
        self.assertEqual(batch.azure_task_id, "task-1")
        self.assertEqual(batch.azure_pool_id, "pool-1")
        self.assertEqual(run.workflow_state, "processing")
        self.assertEqual(run.job_id, "job-1")
        self.assertEqual(run.task_id, "task-1")
        self.assertEqual(storage.upload_bytes.call_count, 3)
        self.assertEqual(submit.call_args.kwargs["analysis_type"], "Nanopore")
        self.assertEqual(
            submit.call_args.kwargs["environment_variables"],
            {
                "FOODPORT_INPUT_SAS_URL": "input-read-url",
                "FOODPORT_OUTPUT_SAS_URL": "output-write-url",
            },
        )
        command = submit.call_args.kwargs["command"]
        self.assertIn(
            '--manifest-prefix "nanopore-test/input/manifests/input-manifest-v"',
            command,
        )
        self.assertIn(
            '--control-blob "nanopore-test/input/control/state.json"',
            command,
        )

    @override_settings(
        NANOPORE_BATCH_COMMAND="wrapper --run {run_name}",
        NANOPORE_RAW_CONTAINER="nanopore-raw",
        NANOPORE_RESULTS_CONTAINER="nanopore-results",
        NANOPORE_BATCH_VM_SIZE="Standard_NC24ads_A100_v4",
        NANOPORE_REFERENCE="references/panel.fasta",
        NANOPORE_BARCODE_VALUES="12",
        NANOPORE_BARCODE_KIT="SQK-RBK114-24",
        NANOPORE_DORADO_MODEL="dorado-model",
    )
    def test_submit_retries_transient_capacity_failure(self):
        run = self.create_run()
        batch = self.create_batch(run)
        storage = mock.Mock()
        storage.create_transfer_url.side_effect = [
            "input-read-url",
            "output-write-url",
        ]
        capacity_failure = {
            "status": "failed",
            "error": "Azure capacity is temporarily unavailable.",
            "error_code": "capacity_unavailable",
            "retryable": True,
        }

        with (
            mock.patch(
                "olc_webportalv2.cowbat.tasks.get_storage_client",
                return_value=storage,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks.generic_api_submit",
                return_value=capacity_failure,
            ),
            mock.patch.object(
                submit_nanopore_batch,
                "retry",
                side_effect=RuntimeError("celery retry scheduled"),
            ) as retry,
            mock.patch.object(submit_nanopore_batch.request, "retries", 0),
        ):
            with self.assertRaisesRegex(RuntimeError, "retry scheduled"):
                submit_nanopore_batch.run(run.pk)

        batch.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(batch.status, "submitting")
        self.assertIn("temporarily unavailable", batch.error)
        self.assertEqual(run.task_retry_count, 1)
        retry.assert_called_once()
        self.assertEqual(retry.call_args.kwargs["countdown"], 60)

    @override_settings(
        NANOPORE_BATCH_COMMAND="wrapper --run {run_name}",
        NANOPORE_RAW_CONTAINER="nanopore-raw",
        NANOPORE_RESULTS_CONTAINER="nanopore-results",
        NANOPORE_BATCH_VM_SIZE="Standard_NC24ads_A100_v4",
        NANOPORE_REFERENCE="references/panel.fasta",
        NANOPORE_BARCODE_VALUES="12",
        NANOPORE_BARCODE_KIT="SQK-RBK114-24",
        NANOPORE_DORADO_MODEL="dorado-model",
    )
    def test_submit_marks_capacity_failure_after_retry_exhaustion(self):
        run = self.create_run()
        batch = self.create_batch(run)
        storage = mock.Mock()
        storage.create_transfer_url.side_effect = [
            "input-read-url",
            "output-write-url",
        ]
        capacity_failure = {
            "status": "failed",
            "error": "Azure capacity is temporarily unavailable.",
            "error_code": "capacity_unavailable",
            "retryable": True,
        }
        max_retries = submit_nanopore_batch.max_retries

        with (
            mock.patch(
                "olc_webportalv2.cowbat.tasks.get_storage_client",
                return_value=storage,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks.generic_api_submit",
                return_value=capacity_failure,
            ),
            mock.patch.object(submit_nanopore_batch, "retry") as retry,
            mock.patch.object(
                submit_nanopore_batch.request, "retries", max_retries
            ),
        ):
            submit_nanopore_batch.run(run.pk)

        batch.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(batch.status, "failed")
        self.assertIn(f"after {max_retries} retries", batch.error)
        self.assertEqual(run.workflow_state, "error")
        retry.assert_not_called()

    @override_settings(NANOPORE_BATCH_COMMAND="")
    def test_submit_marks_run_error_when_wrapper_command_is_unconfigured(self):
        run = self.create_run()
        batch = self.create_batch(run)

        submit_nanopore_batch.run(run.pk)

        batch.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(batch.status, "failed")
        self.assertIn("not configured", batch.error)
        self.assertEqual(run.workflow_state, "error")

    @override_settings(
        NANOPORE_BATCH_COMMAND="wrapper --run {run_name}",
        NANOPORE_RAW_CONTAINER="nanopore-raw",
        NANOPORE_RESULTS_CONTAINER="nanopore-results",
        NANOPORE_REFERENCE="references/panel.fasta",
        NANOPORE_BARCODE_VALUES="12",
        NANOPORE_BARCODE_KIT="SQK-RBK114-24",
        NANOPORE_DORADO_MODEL="dorado-model",
    )
    def test_active_wrapper_publishes_next_generation_without_resubmitting(self):
        run = self.create_run()
        self.create_batch(
            run, generation=1, status="submitted", azure_job_id="job-1"
        )
        pending = self.create_batch(run, generation=2, status="pending")
        storage = mock.Mock()
        storage.create_transfer_url.side_effect = [
            "input-read-url",
            "output-write-url",
        ]

        with (
            mock.patch(
                "olc_webportalv2.cowbat.tasks.get_storage_client",
                return_value=storage,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks.generic_api_submit"
            ) as submit,
        ):
            submit_nanopore_batch.run(run.pk)

        pending.refresh_from_db()
        self.assertEqual(pending.status, "published")
        self.assertIsNotNone(pending.submitted_at)
        submit.assert_not_called()
        self.assertEqual(storage.upload_bytes.call_count, 3)

    def test_monitor_completes_drained_run_after_wrapper_and_job_cleanup(self):
        run = self.create_run(workflow_state="stopping")
        run.nanopore_run.status = "stopping"
        run.nanopore_run.save(update_fields=["status"])
        batch = self.create_batch(
            run,
            status="running",
            azure_job_id="job-1",
            azure_task_id="task-1",
            last_completed_at=timezone.now(),
        )
        batch.exit_code = 0
        batch.report = {"scheduler_exit_code": 0}
        batch.save(update_fields=["exit_code", "report"])
        cloud_task = SimpleNamespace(
            id="task-1",
            state="completed",
            execution_info=SimpleNamespace(exit_code=0),
        )
        batch_client = mock.Mock()
        batch_client.task.list.side_effect = [
            [cloud_task],
            BatchServiceError(404, "job not found"),
        ]

        with (
            mock.patch(
                "olc_webportalv2.cowbat.tasks.create_batch_client",
                return_value=batch_client,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks.get_storage_client",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks._write_nanopore_control"
            ) as write_control,
        ):
            check_nanopore_tasks()

        run.refresh_from_db()
        run.nanopore_run.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(run.workflow_state, "complete")
        self.assertEqual(run.status, "Processed")
        self.assertEqual(run.nanopore_run.status, "complete")
        self.assertEqual(batch.status, "complete")
        self.assertEqual(batch.azure_job_id, "")
        self.assertEqual(batch.azure_task_id, "")
        write_control.assert_called_once_with(run, "stopping")

    def test_monitor_marks_generation_identity_mismatch_as_error(self):
        run = self.create_run(workflow_state="processing")
        batch = self.create_batch(
            run,
            generation=1,
            status="submitted",
            azure_job_id="job-1",
            azure_task_id="task-1",
            report_url="result.json",
        )
        batch.finished_at = timezone.now()
        batch.save(update_fields=["finished_at"])
        storage = mock.Mock()
        storage.object_exists.return_value = True
        storage.download_bytes.return_value = json.dumps(
            {
                "run_id": run.pk + 1,
                "run_name": run.run_name,
                "generation": batch.generation,
                "status": "succeeded",
                "scheduler_exit_code": 0,
            }
        ).encode()
        batch_client = mock.Mock()
        batch_client.task.list.side_effect = BatchServiceError(
            404, "job not found"
        )

        with (
            mock.patch(
                "olc_webportalv2.cowbat.tasks.create_batch_client",
                return_value=batch_client,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks.get_storage_client",
                return_value=storage,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks._write_nanopore_control"
            ) as write_control,
        ):
            check_nanopore_tasks()

        run.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(run.workflow_state, "error")
        self.assertEqual(run.failure_stage, "processing")
        self.assertEqual(batch.status, "failed")
        self.assertTrue(
            any("identity mismatch" in error for error in run.errors)
        )
        write_control.assert_called_once_with(run, "error")

    def test_monitor_records_running_wrapper_without_starting_cleanup(self):
        run = self.create_run(workflow_state="submitted")
        batch = self.create_batch(
            run,
            status="submitted",
            azure_job_id="job-1",
            azure_task_id="task-1",
            last_completed_at=timezone.now(),
        )
        cloud_task = SimpleNamespace(id="task-1", state="running")
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [cloud_task]

        with (
            mock.patch(
                "olc_webportalv2.cowbat.tasks.create_batch_client",
                return_value=batch_client,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks.get_storage_client",
                return_value=mock.Mock(),
            ),
        ):
            check_nanopore_tasks()

        run.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(run.workflow_state, "processing")
        self.assertIsNotNone(run.task_started_at)
        self.assertEqual(batch.status, "running")
        self.assertIsNotNone(batch.started_at)
        batch_client.job.delete.assert_not_called()

    def test_monitor_marks_disappeared_job_as_cleanup_error(self):
        run = self.create_run(workflow_state="processing")
        batch = self.create_batch(
            run,
            status="submitted",
            azure_job_id="job-1",
            azure_task_id="task-1",
            last_completed_at=timezone.now(),
        )
        batch_client = mock.Mock()
        batch_client.task.list.side_effect = BatchServiceError(
            404, "job not found"
        )

        with (
            mock.patch(
                "olc_webportalv2.cowbat.tasks.create_batch_client",
                return_value=batch_client,
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks.get_storage_client",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "olc_webportalv2.cowbat.tasks._write_nanopore_control"
            ),
        ):
            check_nanopore_tasks()

        run.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(run.workflow_state, "error")
        self.assertEqual(run.failure_stage, "processing")
        self.assertIn("disappeared", batch.error)
        self.assertTrue(
            any("disappeared" in error for error in run.errors)
        )


class NanoporeBatchCleanupTests(TestCase):
    def test_cleanup_waits_for_job_deletion_before_pool_deletion(self):
        batch_client = mock.Mock()
        processing_batch = mock.Mock(
            azure_job_id="job-1",
            azure_task_id="task-1",
            azure_pool_id="pool-1",
        )
        run = SimpleNamespace(pk=1, errors=[])

        result = _cleanup_nanopore_batch_resources(
            batch_client=batch_client,
            processing_batch=processing_batch,
            run=run,
        )

        self.assertEqual(result["pending"], True)
        batch_client.job.delete.assert_called_once_with("job-1")
        batch_client.pool.get.assert_not_called()
        self.assertEqual(processing_batch.azure_job_id, "job-1")

    def test_cleanup_clears_confirmed_missing_job_and_pool(self):
        batch_client = mock.Mock()
        missing = BatchServiceError(404, "resource not found")
        batch_client.task.list.side_effect = missing
        batch_client.pool.get.side_effect = missing
        processing_batch = mock.Mock(
            azure_job_id="job-1",
            azure_task_id="task-1",
            azure_pool_id="pool-1",
        )
        run = SimpleNamespace(pk=1, errors=[])

        result = _cleanup_nanopore_batch_resources(
            batch_client=batch_client,
            processing_batch=processing_batch,
            run=run,
        )

        self.assertTrue(result["complete"])
        self.assertEqual(processing_batch.azure_job_id, "")
        self.assertEqual(processing_batch.azure_task_id, "")
        self.assertEqual(processing_batch.azure_pool_id, "")
        self.assertFalse(run.errors)
