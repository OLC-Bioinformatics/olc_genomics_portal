from unittest import mock
from datetime import timedelta
from decimal import Decimal
import os
import shutil
import tempfile

from azure.batch.models import BatchErrorException, TaskState
from django.test import TestCase, override_settings
from django.utils import timezone as django_timezone

from olc_webportalv2.cowbat.models import AzureTask
from olc_webportalv2.cowbat.models import NanoporeFile
from olc_webportalv2.cowbat.models import NanoporeManifest
from olc_webportalv2.cowbat.models import NanoporeProcessingBatch
from olc_webportalv2.cowbat.models import NanoporeRun
from olc_webportalv2.cowbat.models import SequencingRun
from olc_webportalv2.cowbat.models import SummaryMetadata
from olc_webportalv2.cowbat.tasks import (
    cowbat_api_submit,
    check_cowbat_progress,
    check_sample_sheet_format,
    no_sample_sheet,
    nextseq_run,
    read_sections,
    run_cowbat_batch,
    submit_nanopore_batch,
    add_read_filenames_to_metadata,
    build_nanopore_command,
    check_cowbat_tasks,
    check_nanopore_tasks,
    collect_nanopore_iteration,
    _reconcile_nanopore_generation_results,
    cowbat_cleanup,
    handle_incomplete_tasks,
    handle_task_completion,
    load_report,
    send_email,
    submit_batch,
)
from olc_webportalv2.users.models import User


class RunCowbatBatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cowbat-task-test")

    def create_run(self, **kwargs):
        defaults = {
            "user": self.user,
            "run_name": "191218_CAL",
            "seqids": ["SEQ-1"],
        }
        defaults.update(kwargs)
        return SequencingRun.objects.create(**defaults)

    def test_build_nanopore_command_uses_mounted_paths(self):
        command = build_nanopore_command(
            'foodport-nanopore-task --manifest "{manifest_path}" '
            '--source-root "{source_root}" --task-root "{task_root}"',
            manifest_path="/mnt/manifest.json",
            source_root="/mnt/pod5",
            task_root="$AZ_BATCH_TASK_WORKING_DIR",
        )

        self.assertEqual(
            command,
            'foodport-nanopore-task --manifest "/mnt/manifest.json" '
            '--source-root "/mnt/pod5" '
            '--task-root "$AZ_BATCH_TASK_WORKING_DIR"',
        )

    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    @override_settings(NANOPORE_BATCH_COMMAND="foodport-nanopore-task")
    def test_nanopore_submission_failure_remains_retryable(self, blob_service_class):
        run = self.create_run(
            run_name="260825-nanopore",
            workflow_state="ready",
        )
        NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "sample.pod5"}],
            sha256="manifest-sha",
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status="pending",
        )
        blob_service_class.return_value.create_blob_from_text.side_effect = (
            RuntimeError("temporary storage failure")
        )

        submit_nanopore_batch.run(run.pk)

        processing_batch = NanoporeProcessingBatch.objects.get(sequencing_run=run)
        run.refresh_from_db()
        self.assertEqual(processing_batch.status, "failed")
        self.assertIn("temporary storage failure", processing_batch.error)
        self.assertEqual(run.workflow_state, "error")

    @mock.patch("olc_webportalv2.cowbat.tasks.requests.post")
    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    @mock.patch.object(submit_nanopore_batch, "retry")
    @override_settings(
        NANOPORE_BATCH_COMMAND="foodport-nanopore-task",
        NANOPORE_BARCODE_VALUES="22",
    )
    def test_nanopore_capacity_failure_schedules_retry(
        self, retry, blob_service_class, post
    ):
        run = self.create_run(
            run_name="260825-nanopore-capacity",
            workflow_state="ready",
        )
        NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "sample.pod5"}],
            sha256="manifest-sha",
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status="pending",
        )
        response = mock.Mock(status_code=503)
        response.json.return_value = {
            "error": "Azure capacity is temporarily unavailable.",
            "error_code": "capacity_unavailable",
            "retryable": True,
        }
        post.return_value = response
        retry.side_effect = RuntimeError("celery retry scheduled")

        with self.assertRaisesRegex(RuntimeError, "retry scheduled"):
            submit_nanopore_batch.run(run.pk)

        processing_batch = NanoporeProcessingBatch.objects.get(sequencing_run=run)
        run.refresh_from_db()
        self.assertEqual(processing_batch.status, "submitting")
        self.assertIn("temporarily unavailable", processing_batch.error)
        self.assertEqual(run.task_retry_count, 1)
        self.assertEqual(retry.call_count, 1)

    @mock.patch("olc_webportalv2.cowbat.tasks.requests.post")
    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    @mock.patch.object(submit_nanopore_batch, "retry")
    @override_settings(
        NANOPORE_BATCH_COMMAND="foodport-nanopore-task",
        NANOPORE_BARCODE_VALUES="22",
    )
    def test_nanopore_capacity_failure_exhausts_retries(
        self, retry, blob_service_class, post
    ):
        run = self.create_run(
            run_name="260825-nanopore-capacity-exhausted",
            workflow_state="ready",
        )
        NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "sample.pod5"}],
            sha256="manifest-sha",
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status="pending",
        )
        response = mock.Mock(status_code=503)
        response.json.return_value = {
            "error": "Azure capacity is temporarily unavailable.",
            "error_code": "capacity_unavailable",
            "retryable": True,
        }
        post.return_value = response

        with mock.patch.object(submit_nanopore_batch.request, "retries", 8):
            submit_nanopore_batch.run(run.pk)

        processing_batch = NanoporeProcessingBatch.objects.get(sequencing_run=run)
        run.refresh_from_db()
        self.assertEqual(processing_batch.status, "failed")
        self.assertIn("after 8 retries", processing_batch.error)
        self.assertEqual(run.workflow_state, "error")
        self.assertEqual(retry.call_count, 0)

    @mock.patch("olc_webportalv2.cowbat.tasks.requests.post")
    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    @override_settings(
        NANOPORE_BATCH_COMMAND=(
            'foodport-nanopore-task --manifest-blob "{manifest_blob}" '
            '--input-container "{input_container}" '
            '--output-container "{output_container}" '
            '--source-root "{source_root}" --task-root "{task_root}"'
        ),
        NANOPORE_BARCODE_VALUES="22",
        NANOPORE_BATCH_VM_SIZE="Standard_NV18ads_A10_v5",
        NANOPORE_RAW_CONTAINER="nanopore-runs",
        NANOPORE_RESULTS_CONTAINER="nanopore-results",
    )
    def test_nanopore_submission_uses_wrapper_contract(self, blob_service_class, post):
        run = self.create_run(
            run_name="260825-nanopore",
            workflow_state="ready",
        )
        NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[
                {
                    "relative_path": "pass/sample.pod5",
                    "blob_name": (
                        "nanopore-runs/260825-nanopore/input/pod5/pass/sample.pod5"
                    ),
                    "size_bytes": 123,
                    "sha256": "",
                }
            ],
            sha256="manifest-sha",
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status="pending",
        )
        response = mock.Mock()
        response.json.return_value = {
            "pool_id": "pool-1",
            "job_id": "job-1",
            "tasks": ["task-1"],
            "status": "Success",
        }
        post.return_value = response

        submit_nanopore_batch.run(run.pk)

        blob_calls = (
            blob_service_class.return_value.create_blob_from_text.call_args_list
        )
        self.assertEqual(len(blob_calls), 3)
        for call in blob_calls:
            self.assertNotIn("overwrite", call[1])
        request_data = post.call_args[1]["data"]
        self.assertIn("Standard_NV18ads_A10_v5", request_data)
        self.assertIn(
            "nanopore-runs/260825-nanopore/input/manifests/input-manifest-v000001.json",
            request_data,
        )
        self.assertIn("foodport-nanopore-task", request_data)
        self.assertIn("--manifest-prefix", request_data)
        self.assertIn("--control-blob", request_data)
        self.assertIn("runs/260825-nanopore", request_data)
        processing_batch = NanoporeProcessingBatch.objects.get(sequencing_run=run)
        run.refresh_from_db()
        self.assertEqual(processing_batch.status, "submitted")
        self.assertEqual(processing_batch.azure_job_id, "job-1")
        self.assertEqual(run.workflow_state, "processing")

    @mock.patch("olc_webportalv2.cowbat.tasks.batch.BatchServiceClient")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch_auth.SharedKeyCredentials")
    def test_nanopore_monitor_marks_successful_task_complete(
        self, credentials, batch_client_class
    ):
        run = self.create_run(
            run_name="260825-nanopore",
            workflow_state="stopping",
            job_id="job-1",
            task_id="task-1",
        )
        nanopore_run = NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "sample.pod5"}],
            sha256="manifest-sha",
        )
        processing_batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status="submitted",
            azure_pool_id="pool-1",
            azure_job_id="job-1",
            azure_task_id="task-1",
        )
        pending_manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "later.pod5"}],
            sha256="later-manifest-sha",
            generation=2,
        )
        pending_batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=pending_manifest,
            generation=2,
            status="pending",
            pod5_count=1,
        )
        cloud_task = mock.Mock(
            id="task-1",
            state=TaskState.completed,
            execution_info=mock.Mock(exit_code=0),
        )
        batch_client_class.return_value.task.list.return_value = [cloud_task]

        check_nanopore_tasks()

        processing_batch.refresh_from_db()
        run.refresh_from_db()
        nanopore_run.refresh_from_db()
        self.assertEqual(processing_batch.status, "complete")
        self.assertEqual(processing_batch.exit_code, 0)
        self.assertEqual(run.workflow_state, "complete")
        self.assertEqual(run.status, "Processed")
        self.assertEqual(nanopore_run.status, "complete")
        pending_batch.refresh_from_db()
        self.assertEqual(pending_batch.status, "complete")
        self.assertEqual(pending_batch.exit_code, 0)
        self.assertEqual(pending_batch.processed_pod5_count, 1)
        batch_client = batch_client_class.return_value
        batch_client.job.delete.assert_called_once_with(job_id="job-1")
        batch_client.pool.delete.assert_called_once_with(pool_id="pool-1")

    @mock.patch("olc_webportalv2.cowbat.tasks.batch.BatchServiceClient")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch_auth.SharedKeyCredentials")
    def test_nanopore_monitor_cleans_up_failed_task_resources(
        self, credentials, batch_client_class
    ):
        run = self.create_run(
            run_name="260825-nanopore-failed",
            workflow_state="submitted",
        )
        NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "sample.pod5"}],
            sha256="manifest-sha",
        )
        processing_batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status="submitted",
            azure_pool_id="pool-failed",
            azure_job_id="job-failed",
            azure_task_id="task-failed",
        )
        cloud_task = mock.Mock(
            id="task-failed",
            state=TaskState.completed,
            execution_info=mock.Mock(exit_code=2),
        )
        batch_client_class.return_value.task.list.return_value = [cloud_task]

        check_nanopore_tasks()

        processing_batch.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(processing_batch.status, "failed")
        self.assertEqual(run.workflow_state, "error")
        batch_client = batch_client_class.return_value
        batch_client.job.delete.assert_called_once_with(job_id="job-failed")
        batch_client.pool.delete.assert_called_once_with(pool_id="pool-failed")

    @mock.patch("olc_webportalv2.cowbat.tasks.submit_nanopore_batch.apply_async")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch.BatchServiceClient")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch_auth.SharedKeyCredentials")
    def test_nanopore_streaming_failure_retries_pending_generation(
        self, credentials, batch_client_class, submit
    ):
        run = self.create_run(
            run_name="260825-nanopore-retry",
            workflow_state="submitted",
        )
        nanopore_run = NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "sample.pod5"}],
            sha256="manifest-sha",
        )
        processing_batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status="submitted",
            azure_pool_id="pool-failed",
            azure_job_id="job-failed",
            azure_task_id="task-failed",
        )
        pending_manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "later.pod5"}],
            sha256="later-manifest-sha",
            generation=2,
        )
        pending_batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=pending_manifest,
            generation=2,
            status="pending",
            pod5_count=1,
        )
        cloud_task = mock.Mock(
            id="task-failed",
            state=TaskState.completed,
            execution_info=mock.Mock(exit_code=1),
        )
        batch_client_class.return_value.task.list.return_value = [cloud_task]

        check_nanopore_tasks()

        processing_batch.refresh_from_db()
        pending_batch.refresh_from_db()
        run.refresh_from_db()
        nanopore_run.refresh_from_db()
        self.assertEqual(processing_batch.status, "failed")
        self.assertEqual(pending_batch.status, "pending")
        self.assertEqual(run.workflow_state, "processing")
        self.assertEqual(run.status, "Unprocessed")
        self.assertEqual(nanopore_run.status, "processing")
        submit.assert_called_once_with(queue="cowbat", args=(run.pk,))

    @mock.patch("olc_webportalv2.cowbat.tasks.batch.BatchServiceClient")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch_auth.SharedKeyCredentials")
    def test_nanopore_success_completes_pending_generations_with_one_task(
        self, credentials, batch_client_class
    ):
        run = self.create_run(
            run_name="260825-nanopore-success-pending",
            workflow_state="stopping",
        )
        nanopore_run = NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "sample.pod5"}],
            sha256="manifest-sha",
        )
        processing_batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status="submitted",
            azure_pool_id="pool-complete",
            azure_job_id="job-complete",
            azure_task_id="task-complete",
        )
        pending_manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "later.pod5"}],
            sha256="later-manifest-sha",
            generation=2,
        )
        pending_batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=pending_manifest,
            generation=2,
            status="pending",
            pod5_count=1,
        )
        cloud_task = mock.Mock(
            id="task-complete",
            state=TaskState.completed,
            execution_info=mock.Mock(exit_code=0),
        )
        batch_client_class.return_value.task.list.return_value = [cloud_task]

        check_nanopore_tasks()

        processing_batch.refresh_from_db()
        pending_batch.refresh_from_db()
        run.refresh_from_db()
        nanopore_run.refresh_from_db()
        self.assertEqual(processing_batch.status, "complete")
        self.assertEqual(pending_batch.status, "complete")
        self.assertEqual(run.workflow_state, "complete")
        self.assertEqual(run.status, "Processed")
        self.assertEqual(nanopore_run.status, "complete")
        batch_client = batch_client_class.return_value
        batch_client.job.delete.assert_called_once_with(job_id="job-complete")
        batch_client.pool.delete.assert_called_once_with(pool_id="pool-complete")

    def test_nanopore_collection_defers_until_result_is_published(self):
        run = self.create_run(
            run_name="260925-nanopore-gated",
            workflow_state="processing",
        )
        NanoporeRun.objects.create(
            sequencing_run=run,
            status="processing",
            last_file_seen_at=django_timezone.now(),
        )
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "first.pod5"}],
            sha256="first-manifest",
            generation=1,
        )
        active = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            generation=1,
            status="running",
            pod5_count=1,
            azure_job_id="job-1",
            azure_task_id="task-1",
        )
        waiting = NanoporeFile.objects.create(
            sequencing_run=run,
            relative_path="pass/wave-2/later.pod5",
            blob_name="run/input/pod5/pass/wave-2/later.pod5",
            size_bytes=123,
            sha256="later",
            status="uploaded",
        )

        result = collect_nanopore_iteration.run(run.pk, force=True)

        waiting.refresh_from_db()
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(
            result["reason"],
            "previous iteration result is not published",
        )
        self.assertEqual(result["active_batch_id"], active.pk)
        self.assertIsNone(waiting.processing_batch_id)

    def test_nanopore_collection_merges_waiting_waves_after_publication(self):
        run = self.create_run(
            run_name="260925-nanopore-merge",
            workflow_state="processing",
        )
        NanoporeRun.objects.create(
            sequencing_run=run,
            status="processing",
            last_file_seen_at=django_timezone.now(),
        )
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "first.pod5"}],
            sha256="first-manifest",
            generation=1,
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            generation=1,
            status="running",
            pod5_count=1,
            last_completed_at=django_timezone.now(),
            processed_pod5_count=1,
            azure_job_id="job-1",
            azure_task_id="task-1",
        )
        waiting = [
            NanoporeFile.objects.create(
                sequencing_run=run,
                relative_path="pass/wave-{0}/file-{0}.pod5".format(wave),
                blob_name=(
                    "run/input/pod5/pass/wave-{0}/file-{0}.pod5"
                ).format(wave),
                size_bytes=100 + wave,
                sha256="file-{0}".format(wave),
                status="uploaded",
            )
            for wave in (2, 3, 4)
        ]

        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(
                "olc_webportalv2.cowbat.tasks."
                "submit_nanopore_batch.apply_async"
            ) as submit:
                result = collect_nanopore_iteration.run(run.pk, force=True)

        merged = NanoporeProcessingBatch.objects.get(
            sequencing_run=run,
            generation=2,
        )
        self.assertEqual(result["status"], "queued")
        self.assertEqual(merged.pod5_count, 3)
        self.assertEqual(len(merged.manifest.files), 3)
        self.assertEqual(
            set(NanoporeFile.objects.filter(
                pk__in=[item.pk for item in waiting]
            ).values_list("processing_batch_id", flat=True)),
            {merged.pk},
        )
        submit.assert_called_once_with(queue="cowbat", args=(run.pk,))

    def test_nanopore_result_publication_unlocks_next_collection(self):
        run = self.create_run(
            run_name="260925-nanopore-result",
            workflow_state="processing",
        )
        NanoporeRun.objects.create(sequencing_run=run, status="processing")
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[{"relative_path": "first.pod5"}],
            sha256="first-manifest",
            generation=1,
        )
        processing_batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            generation=1,
            status="running",
            pod5_count=3,
            report_url=(
                "runs/260925-nanopore-result/iterations/"
                "iteration-000001/manifests/iteration-000001.json"
            ),
        )
        blob_service = mock.Mock()
        blob_service.exists.return_value = True
        blob_service.get_blob_to_text.return_value = mock.Mock(
            content=(
                '{"status":"completed","scheduler_exit_code":0,'
                '"generation":1}'
            )
        )

        completed, failed = _reconcile_nanopore_generation_results(
            run,
            blob_service,
        )

        processing_batch.refresh_from_db()
        self.assertEqual([item.pk for item in completed], [processing_batch.pk])
        self.assertEqual(failed, [])
        self.assertIsNotNone(processing_batch.last_completed_at)
        self.assertEqual(processing_batch.processed_pod5_count, 3)
        self.assertEqual(processing_batch.status, "running")

    @mock.patch("olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True)
    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    def test_marks_run_upload_error_when_read_pair_is_incomplete(
        self, blob_service_class, isdir
    ):
        run = self.create_run()
        blob_service_class.return_value.list_blobs.return_value = [
            mock.Mock(name="SEQ-1_R1.fastq"),
        ]
        blob_service_class.return_value.list_blobs.return_value[
            0
        ].name = "SEQ-1_R1.fastq"

        run_cowbat_batch.run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "UploadError")

    @mock.patch("olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True)
    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    @mock.patch("olc_webportalv2.cowbat.tasks.nextseq_run")
    def test_nextseq_run_uses_nextseq_branch(
        self, nextseq_run, blob_service_class, isdir
    ):
        run = self.create_run(nextseq=True)
        blob_service_class.return_value.list_blobs.return_value = [
            mock.Mock(name="SEQ-1_R1.fastq"),
            mock.Mock(name="SEQ-1_R2.fastq"),
        ]
        blob_service_class.return_value.list_blobs.return_value[
            0
        ].name = "SEQ-1_R1.fastq"
        blob_service_class.return_value.list_blobs.return_value[
            1
        ].name = "SEQ-1_R2.fastq"

        run_cowbat_batch.run(run.pk)

        self.assertEqual(nextseq_run.call_count, 1)
        self.assertEqual(nextseq_run.call_args[1]["container_name"], "191218-cal")
        self.assertEqual(nextseq_run.call_args[1]["sequencing_run"].pk, run.pk)
        run.refresh_from_db()
        self.assertIsNotNone(run.task_started_at)
        self.assertIsNotNone(run.task_finished_at)
        self.assertEqual(run.task_queue, "cowbat")
        self.assertEqual(run.task_retry_count, 1)

    @mock.patch("olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True)
    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    @mock.patch("olc_webportalv2.cowbat.tasks.submit_batch")
    def test_submits_complete_non_nextseq_run(
        self, submit_batch, blob_service_class, isdir
    ):
        run = self.create_run()
        blob_service_class.return_value.list_blobs.return_value = [
            mock.Mock(name="SEQ-1_R1.fastq"),
            mock.Mock(name="SEQ-1_R2.fastq"),
        ]
        blob_service_class.return_value.list_blobs.return_value[
            0
        ].name = "SEQ-1_R1.fastq"
        blob_service_class.return_value.list_blobs.return_value[
            1
        ].name = "SEQ-1_R2.fastq"

        run_cowbat_batch.run(run.pk)

        self.assertEqual(submit_batch.call_count, 1)
        self.assertEqual(submit_batch.call_args[1]["sequencing_run"].pk, run.pk)
        self.assertEqual(
            submit_batch.call_args[1]["run_folder"], "olc_webportalv2/media/191218_CAL"
        )

    @mock.patch("olc_webportalv2.cowbat.tasks.cowbat_api_submit")
    @mock.patch("olc_webportalv2.cowbat.tasks.no_sample_sheet")
    def test_nextseq_subruns_inherit_parent_task_metadata(
        self, no_sample_sheet, cowbat_api_submit
    ):
        submitted_at = django_timezone.now()
        run = self.create_run(
            run_name="260911-vl00772",
            nextseq=True,
            user=self.user,
            task_submitted_at=submitted_at,
            task_queue="cowbat",
            emails_array=["operator@example.test"],
        )
        no_sample_sheet.return_value = {
            1: ["SEQ-1"],
            2: ["SEQ-2"],
        }

        nextseq_run(
            blob_client=mock.Mock(),
            blob_files=["SEQ-1_R1.fastq.gz"],
            container_name=run.container,
            sequencing_run=run,
        )

        sub_run = SequencingRun.objects.get(run_name="260911-vl00772-1")
        self.assertEqual(sub_run.user_id, run.user_id)
        self.assertEqual(sub_run.status, "Processing")
        self.assertEqual(sub_run.workflow_state, "submitted")
        self.assertEqual(sub_run.task_submitted_at, submitted_at)
        self.assertEqual(
            sub_run.task_started_at,
            submitted_at + timedelta(seconds=1),
        )
        self.assertEqual(sub_run.task_queue, "cowbat")
        self.assertEqual(sub_run.emails_array, ["operator@example.test"])
        self.assertEqual(cowbat_api_submit.call_count, 2)

    @mock.patch("olc_webportalv2.cowbat.tasks.copy_blobs")
    @mock.patch(
        "olc_webportalv2.cowbat.tasks.create_sub_container",
        side_effect=lambda blob_client, container_name, i: "{}-{}".format(
            container_name, i
        ),
    )
    def test_no_sample_sheet_groups_samples_and_copies_reads(
        self, create_sub_container, copy_blobs
    ):
        blob_files = [
            "SAMPLE-B_R1.fastq.gz",
            "SAMPLE-B_R2.fastq.gz",
            "SAMPLE-A_R1.fastq.gz",
            "SAMPLE-A_R2.fastq.gz",
            "notes.txt",
        ]

        result = no_sample_sheet(
            blob_client=mock.Mock(),
            blob_files=blob_files,
            container_name="run-1",
            max_samples=1,
        )

        self.assertEqual(result, {1: ["SAMPLE-A"], 2: ["SAMPLE-B"]})
        self.assertEqual(create_sub_container.call_count, 2)
        self.assertEqual(copy_blobs.call_count, 2)
        self.assertEqual(copy_blobs.call_args_list[0][1]["sample_name"], "SAMPLE-A")
        self.assertEqual(copy_blobs.call_args_list[1][1]["sample_name"], "SAMPLE-B")

    @mock.patch("olc_webportalv2.cowbat.tasks.copy_blobs")
    @mock.patch(
        "olc_webportalv2.cowbat.tasks.create_sub_container",
        side_effect=lambda blob_client, container_name, i: "{}-{}".format(
            container_name, i
        ),
    )
    def test_no_sample_sheet_balances_samples_across_subruns(
        self, create_sub_container, copy_blobs
    ):
        blob_files = [
            "SAMPLE-{0:03d}_R{1}.fastq.gz".format(sample, read)
            for sample in range(96)
            for read in (1, 2)
        ]

        result = no_sample_sheet(
            blob_client=mock.Mock(),
            blob_files=blob_files,
            container_name="run-1",
            max_samples=40,
        )

        self.assertEqual(
            [len(result[index]) for index in sorted(result)],
            [32, 32, 32],
        )
        self.assertEqual(sum(len(samples) for samples in result.values()), 96)
        self.assertEqual(create_sub_container.call_count, 3)
        self.assertEqual(copy_blobs.call_count, 96)

    @mock.patch("olc_webportalv2.cowbat.tasks.cowbat_api_submit")
    def test_submit_batch_builds_pipeline_command(self, api_submit):
        run = self.create_run(basic_assembly=True, preprocess=True)
        run.container = "191218-cal"
        run.save()

        submit_batch(
            run_folder="/tmp/run",
            sequencing_run=run,
            vm_size="Standard_D4s_v3",
        )

        self.assertEqual(api_submit.call_count, 1)
        command = api_submit.call_args[1]["command"]
        self.assertIn("assembly_pipeline.py", command)
        self.assertIn(" -b", command)
        self.assertIn(" -p", command)
        self.assertEqual(api_submit.call_args[1]["vm_size"], "Standard_D4s_v3")

    @mock.patch("olc_webportalv2.cowbat.tasks.requests.post")
    def test_cowbat_api_submit_persists_batch_response(self, post):
        run = self.create_run()
        response = post.return_value
        response.json.return_value = {
            "pool_id": "pool-1",
            "job_id": "job-1",
            "tasks": ["task-1"],
            "status": "Submitted",
            "error": "",
        }

        cowbat_api_submit(
            command="assemble",
            run_folder="/tmp/run",
            sequencing_run=run,
            vm_size="Standard_D4s_v3",
        )

        run.refresh_from_db()
        self.assertEqual(run.pool_id, "pool-1")
        self.assertEqual(run.job_id, "job-1")
        self.assertEqual(run.task_id, "task-1")
        self.assertEqual(run.batch_submit_status, "Submitted")
        self.assertEqual(run.batch_submit_errors, "")
        self.assertEqual(run.azuretask.count(), 1)
        self.assertEqual(post.call_count, 1)

    def test_check_sample_sheet_format_detects_target(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as sample_sheet:
            sample_sheet.write("[Header]\n[BCLConvert_Data]\n")
            path = sample_sheet.name

        try:
            self.assertTrue(check_sample_sheet_format(path))
            self.assertFalse(check_sample_sheet_format(path, "[Missing]"))
        finally:
            os.unlink(path)

    def test_read_sections_parses_simple_sections(self):
        content = "[Header]\nRunName,Run-1\n\n[Reads]\nRead1Cycles,151\n"
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as sample_sheet:
            sample_sheet.write(content)
            path = sample_sheet.name

        try:
            result = read_sections(path)
        finally:
            os.unlink(path)

        self.assertEqual(result["Header"]["RunName"], "Run-1")
        self.assertEqual(result["Reads"]["Read1Cycles"], 151)

    def test_read_sections_rejects_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            read_sections("/tmp/no-such-cowbat-sample-sheet.csv")

    def test_load_report_marks_missing_report_as_error(self):
        run = self.create_run()

        self.assertIsNone(load_report("/tmp/no-summary-report.csv", run))

        run.refresh_from_db()
        self.assertEqual(run.status, "Error")
        self.assertEqual(run.errors, ["Summary report not found in blob storage."])

    def test_load_report_creates_summary_metadata(self):
        headers = [
            "SeqID",
            "SampleName",
            "Genus",
            "E_coli_Serotype",
            "SISTR_serovar",
            "GeneSeekr_Profile",
            "Vtyper_Profile",
            "rMLST_Result",
            "MLST_Result",
            "N50",
            "NumContigs",
            "TotalLength",
            "AverageCoverageDepth",
            "ConfindrContamSNVs",
            "SequencingDate",
            "Analyst",
            "Flowcell",
            "MachineName",
            "AssemblyDate",
            "PipelineVersion",
            "Database",
        ]
        values = ["SEQ-1", "Sample 1", "Escherichia"] + ["value"] * 19
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as report:
            report.write(",".join(headers) + "\n")
            report.write(",".join(values) + "\n")
            report_path = report.name

        run = self.create_run()
        try:
            self.assertTrue(load_report(report_path, run))
        finally:
            os.unlink(report_path)

        summary = SummaryMetadata.objects.get(sequencing_run=run)
        summary_row = list(summary.summary_results.values())[0]
        self.assertEqual(summary_row["SeqID"], "SEQ-1")
        self.assertEqual(summary_row["SampleName"], "Sample 1")
        self.assertEqual(summary_row["Genus"], "Escherichia")

    def test_add_read_filenames_updates_report_and_uploads_it(self):
        run = self.create_run()
        with tempfile.TemporaryDirectory() as reports_folder:
            report_path = os.path.join(reports_folder, "combinedMetadata.csv")
            with open(report_path, "w") as report:
                report.write("SeqID,SampleName\nSEQ-1,Sample 1\n")

            blob_client = mock.Mock()
            add_read_filenames_to_metadata(
                sequencing_run=run,
                blob_client=blob_client,
                container_name="run-1",
                reports_folder=reports_folder,
                blob_filenames=[
                    "SEQ-1_R1.fastq.gz",
                    "SEQ-1_R2.fastq.gz",
                    "SEQ-1_R1.trimmed.fastq.gz",
                ],
            )

            with open(report_path) as report:
                report_contents = report.read()

        self.assertIn("R1_file,R2_file", report_contents)
        self.assertIn("SEQ-1_R1.fastq.gz,SEQ-1_R2.fastq.gz", report_contents)
        self.assertEqual(run.errors, [])
        self.assertEqual(blob_client.create_blob_from_path.call_count, 1)

    def test_add_read_filenames_ignores_missing_report(self):
        run = self.create_run()
        blob_client = mock.Mock()

        add_read_filenames_to_metadata(
            sequencing_run=run,
            blob_client=blob_client,
            container_name="run-1",
            reports_folder=tempfile.gettempdir(),
            blob_filenames=[],
        )

        blob_client.create_blob_from_path.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.errors, [])

    def test_add_read_filenames_preserves_existing_and_reports_ambiguous_reads(self):
        run = self.create_run()
        with tempfile.TemporaryDirectory() as reports_folder:
            report_path = os.path.join(reports_folder, "combinedMetadata.csv")
            with open(report_path, "w") as report:
                report.write(
                    "SeqID,SampleName,R1_file,R2_file\n"
                    "SEQ-1,Sample 1,existing_R1.fastq.gz,\n"
                )

            add_read_filenames_to_metadata(
                sequencing_run=run,
                blob_client=mock.Mock(),
                container_name="run-1",
                reports_folder=reports_folder,
                blob_filenames=[
                    "SEQ-1_R1_a.fastq.gz",
                    "SEQ-1_R1_b.fastq.gz",
                    "SEQ-1_R2.fastq.gz",
                ],
            )

            with open(report_path) as report:
                report_contents = report.read()

        self.assertIn("existing_R1.fastq.gz,SEQ-1_R2.fastq.gz", report_contents)
        self.assertIn("SEQ-1_R1_a.fastq.gz", run.errors[0])
        self.assertIn("SEQ-1_R1_b.fastq.gz", run.errors[0])

    @mock.patch("olc_webportalv2.cowbat.tasks.cowbat_cleanup")
    def test_handle_task_completion_schedules_cleanup_for_success(self, cleanup):
        run = self.create_run(pool_id="pool-1", job_id="job-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [
            mock.Mock(execution_info=mock.Mock(exit_code=0)),
        ]

        handle_task_completion(
            batch_client=batch_client,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

        run.refresh_from_db()
        self.assertEqual(run.exit_code, 0)
        self.assertEqual(run.status, "Unprocessed")
        self.assertEqual(AzureTask.objects.filter(pk=azure_task.id).count(), 0)
        self.assertEqual(cleanup.apply_async.call_count, 1)
        batch_client.job.delete.assert_called_once_with(job_id="job-1")
        batch_client.pool.delete.assert_called_once_with(pool_id="pool-1")

    @mock.patch("olc_webportalv2.cowbat.tasks.cowbat_cleanup")
    def test_handle_task_completion_marks_run_error_for_failed_task(self, cleanup):
        run = self.create_run(pool_id="pool-1", job_id="job-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [
            mock.Mock(execution_info=mock.Mock(exit_code=7)),
        ]

        handle_task_completion(
            batch_client=batch_client,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

        run.refresh_from_db()
        self.assertEqual(run.exit_code, 7)
        self.assertEqual(run.status, "Error")
        self.assertIn("Exit code bad", run.errors)
        self.assertEqual(AzureTask.objects.filter(pk=azure_task.id).count(), 0)
        self.assertEqual(cleanup.apply_async.call_count, 0)

    @mock.patch("olc_webportalv2.cowbat.tasks.cowbat_cleanup")
    def test_handle_task_completion_records_azure_cleanup_errors(self, cleanup):
        run = self.create_run(pool_id="pool-1", job_id="job-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [
            mock.Mock(execution_info=mock.Mock(exit_code=0)),
        ]
        batch_error = BatchErrorException(lambda *args: None, mock.Mock())
        batch_client.job.delete.side_effect = batch_error
        batch_client.pool.delete.side_effect = batch_error

        handle_task_completion(
            batch_client=batch_client,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

        run.refresh_from_db()
        self.assertIn("Terminating job error:", run.errors)
        self.assertIn("Terminating pool error:", run.errors)
        self.assertEqual(AzureTask.objects.filter(pk=azure_task.id).count(), 0)
        self.assertEqual(cleanup.apply_async.call_count, 1)

    @mock.patch("olc_webportalv2.cowbat.tasks.check_cowbat_progress")
    def test_handle_incomplete_tasks_checks_progress_for_active_pool(
        self, check_progress
    ):
        run = self.create_run(task_id="task-1")
        pool = mock.Mock(id="job-1", resize_errors=[])
        batch_client = mock.Mock()
        batch_client.pool.list.return_value = [pool]

        handle_incomplete_tasks(
            batch_client=batch_client,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=1,
        )

        self.assertEqual(run.status, "Unprocessed")
        check_progress.assert_called_once_with(
            batch_client,
            "job-1",
            "task-1",
            run,
        )

    @mock.patch("olc_webportalv2.cowbat.tasks.sleep")
    @mock.patch("olc_webportalv2.cowbat.tasks.delete_pool_job")
    @mock.patch("olc_webportalv2.cowbat.tasks.check_cowbat_progress")
    def test_handle_incomplete_tasks_marks_resize_error(
        self, check_progress, delete_pool_job, sleep
    ):
        run = self.create_run(task_id="task-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        pool = mock.Mock(id="job-1", resize_errors=["quota"])
        batch_client = mock.Mock()
        batch_client.pool.list.return_value = [pool]

        handle_incomplete_tasks(
            batch_client=batch_client,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

        run.refresh_from_db()
        self.assertEqual(run.status, "Error")
        self.assertEqual(run.workflow_state, "error")
        self.assertEqual(run.failure_stage, "submission")
        self.assertIsNotNone(run.task_finished_at)
        self.assertIn("pool resize failed", run.errors[0])
        self.assertEqual(AzureTask.objects.filter(pk=azure_task.id).count(), 0)
        delete_pool_job.assert_called_once_with(
            batch_client=batch_client,
            batch_job_name="job-1",
        )
        sleep.assert_called_once_with(60)
        check_progress.assert_not_called()

    @mock.patch("olc_webportalv2.cowbat.tasks.handle_resize_error")
    def test_missing_resize_error_pool_finalizes_run_and_deletes_task(
        self, handle_resize_error
    ):
        run = self.create_run(
            task_id="task-1",
            job_id="job-1",
            pool_id="job-1",
            status="Resize Error",
        )
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client = mock.Mock()
        batch_client.pool.list.return_value = []

        handle_incomplete_tasks(
            batch_client=batch_client,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

        run.refresh_from_db()
        self.assertEqual(run.status, "Error")
        self.assertEqual(run.workflow_state, "error")
        self.assertEqual(AzureTask.objects.filter(pk=azure_task.id).count(), 0)
        handle_resize_error.assert_not_called()

    def test_check_cowbat_progress_updates_run_and_saves_task_files(self):
        run = self.create_run(task_id="task-1")
        stderr_file = mock.Mock(name="stderr.txt")
        report_file = mock.Mock(name="reports/summary.csv")
        stderr_file.name = "stderr.txt"
        report_file.name = "reports/summary.csv"
        batch_client = mock.Mock()
        batch_client.file.list_from_task.return_value = [
            stderr_file,
            report_file,
        ]
        batch_client.file.get_from_task.side_effect = [
            [b"INFO 1 Running assembly\nINFO 2 Running assembly\n"],
            [b"INFO 1 Running assembly\nINFO 2 Running assembly\n"],
            [b"report contents\n"],
        ]

        try:
            check_cowbat_progress(
                batch_client=batch_client,
                batch_job_name="progress-test-job",
                batch_task_name="task-1",
                sequencing_run=run,
            )
        finally:
            shutil.rmtree(
                os.path.join(
                    "olc_webportalv2",
                    "media",
                    "cowbat",
                    "progress-test-job",
                ),
                ignore_errors=True,
            )

        run.refresh_from_db()
        self.assertEqual(run.status, "Processing")
        self.assertEqual(run.progress, "Running assembly")
        self.assertEqual(run.percent_complete, Decimal("0.01"))
        self.assertEqual(batch_client.file.list_from_task.call_count, 2)
        self.assertEqual(
            batch_client.file.list_from_task.call_args[1],
            {
                "job_id": "progress-test-job",
                "task_id": "task-1",
                "recursive": True,
            },
        )

    def test_check_cowbat_progress_updates_mapped_percentage(self):
        run = self.create_run(task_id="task-1")
        stderr_file = mock.Mock(name="stderr.txt")
        stderr_file.name = "stderr.txt"
        batch_client = mock.Mock()
        batch_client.file.list_from_task.return_value = [stderr_file]
        batch_client.file.get_from_task.side_effect = [
            [b"INFO 1 Performing mash analyses\n"],
            [b"INFO 1 Performing mash analyses\n"],
        ]

        try:
            check_cowbat_progress(
                batch_client=batch_client,
                batch_job_name="progress-test-job",
                batch_task_name="task-1",
                sequencing_run=run,
            )
        finally:
            shutil.rmtree(
                os.path.join(
                    "olc_webportalv2",
                    "media",
                    "cowbat",
                    "progress-test-job",
                ),
                ignore_errors=True,
            )

        run.refresh_from_db()
        self.assertEqual(run.progress, "Performing mash analyses")
        self.assertEqual(run.percent_complete, Decimal("66.90"))

    @mock.patch("olc_webportalv2.cowbat.tasks.handle_task_completion")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch.BatchServiceClient")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch_auth.SharedKeyCredentials")
    def test_check_cowbat_tasks_routes_completed_tasks(
        self, credentials, batch_client_class, handle_completion
    ):
        run = self.create_run(job_id="job-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client_class.return_value.task.list.return_value = [
            mock.Mock(state=TaskState.completed),
        ]

        check_cowbat_tasks()

        handle_completion.assert_called_once_with(
            batch_client=batch_client_class.return_value,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

    @mock.patch("olc_webportalv2.cowbat.tasks.handle_incomplete_tasks")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch.BatchServiceClient")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch_auth.SharedKeyCredentials")
    def test_check_cowbat_tasks_routes_active_tasks(
        self, credentials, batch_client_class, handle_incomplete
    ):
        run = self.create_run(job_id="job-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client_class.return_value.task.list.return_value = [
            mock.Mock(state="active"),
        ]

        check_cowbat_tasks()

        handle_incomplete.assert_called_once_with(
            batch_client=batch_client_class.return_value,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

    @mock.patch("olc_webportalv2.cowbat.tasks.handle_task_completion")
    @mock.patch("olc_webportalv2.cowbat.tasks.handle_incomplete_tasks")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch.BatchServiceClient")
    @mock.patch("olc_webportalv2.cowbat.tasks.batch_auth.SharedKeyCredentials")
    def test_check_cowbat_tasks_records_batch_error_without_processing_run(
        self,
        credentials,
        batch_client_class,
        handle_incomplete,
        handle_completion,
    ):
        run = self.create_run(job_id="job-1")
        AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client_class.return_value.task.list.side_effect = BatchErrorException(
            lambda *args: None, mock.Mock()
        )

        check_cowbat_tasks()

        run.refresh_from_db()
        self.assertIn("Running task error:", run.errors)
        self.assertEqual(handle_incomplete.call_count, 0)
        self.assertEqual(handle_completion.call_count, 0)

    @mock.patch("olc_webportalv2.cowbat.tasks.smtplib.SMTP")
    @override_settings(ENVIRONMENT="PROD")
    def test_send_email_sends_message(self, smtp_class):
        server = smtp_class.return_value

        send_email("Subject", "Body", "recipient@example.com")

        self.assertEqual(server.starttls.call_count, 1)
        self.assertEqual(server.login.call_count, 1)
        self.assertEqual(server.sendmail.call_count, 1)
        self.assertEqual(server.quit.call_count, 1)

    @mock.patch("olc_webportalv2.cowbat.tasks.shutil.rmtree")
    @mock.patch("olc_webportalv2.cowbat.tasks.shutil.make_archive")
    @mock.patch("olc_webportalv2.cowbat.tasks.generate_download_link")
    @mock.patch("olc_webportalv2.cowbat.tasks.load_report", return_value=True)
    @mock.patch("olc_webportalv2.cowbat.tasks.add_read_filenames_to_metadata")
    @mock.patch("olc_webportalv2.cowbat.tasks.os.listdir", return_value=[])
    @mock.patch("olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True)
    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    @mock.patch("olc_webportalv2.cowbat.tasks.send_email")
    @override_settings(ENVIRONMENT="PROD")
    def test_cowbat_cleanup_downloads_reports_and_completes_run(
        self,
        send_email_mock,
        blob_service_class,
        isdir,
        listdir,
        add_read_filenames,
        load_report_mock,
        generate_link,
        make_archive,
        rmtree,
    ):
        run = self.create_run(container="191218-cal")
        blob_names = [
            "BestAssemblies/SEQ-1.fasta",
            "reports/combinedMetadata.csv",
            "reports/summaryMetadata.csv",
            "reports/results.tsv",
            "reports/contigs.fa",
            "reports/results.xlsx",
            "SampleSheet.csv",
            "unrelated.txt",
        ]
        blob_service_class.return_value.list_blobs.return_value = [
            mock.Mock(name=blob_name) for blob_name in blob_names
        ]
        for blob, blob_name in zip(
            blob_service_class.return_value.list_blobs.return_value, blob_names
        ):
            blob.name = blob_name
        generate_link.return_value = "https://example.test/run.zip"

        cowbat_cleanup.run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "Complete")
        self.assertEqual(run.percent_complete, Decimal("100.00"))
        self.assertEqual(run.download_link, "https://example.test/run.zip")
        self.assertEqual(blob_service_class.return_value.get_blob_to_path.call_count, 7)
        self.assertEqual(add_read_filenames.call_count, 1)
        self.assertEqual(load_report_mock.call_count, 1)
        self.assertEqual(make_archive.call_count, 1)
        self.assertEqual(generate_link.call_count, 1)
        self.assertEqual(rmtree.call_count, 1)
        self.assertEqual(send_email_mock.call_count, 5)

    @mock.patch("olc_webportalv2.cowbat.tasks.shutil.rmtree")
    @mock.patch("olc_webportalv2.cowbat.tasks.shutil.make_archive")
    @mock.patch("olc_webportalv2.cowbat.tasks.generate_download_link")
    @mock.patch("olc_webportalv2.cowbat.tasks.add_read_filenames_to_metadata")
    @mock.patch("olc_webportalv2.cowbat.tasks.os.listdir", return_value=[])
    @mock.patch("olc_webportalv2.cowbat.tasks.os.path.isfile", return_value=False)
    @mock.patch("olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True)
    @mock.patch("olc_webportalv2.cowbat.tasks.BlockBlobService")
    @mock.patch("olc_webportalv2.cowbat.tasks.send_email")
    def test_cowbat_cleanup_records_missing_summary_report(
        self,
        send_email_mock,
        blob_service_class,
        isdir,
        isfile,
        listdir,
        add_read_filenames,
        generate_link,
        make_archive,
        rmtree,
    ):
        run = self.create_run(container="191218-cal")
        blob = mock.Mock()
        blob.name = "reports/combinedMetadata.csv"
        blob_service_class.return_value.list_blobs.return_value = [blob]
        generate_link.return_value = "https://example.test/run.zip"

        cowbat_cleanup.run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "Error")
        self.assertEqual(run.errors, ["Summary report not found in blob storage."])
        self.assertEqual(send_email_mock.call_count, 0)
        self.assertEqual(make_archive.call_count, 1)
        self.assertEqual(generate_link.call_count, 1)
        self.assertEqual(rmtree.call_count, 1)
