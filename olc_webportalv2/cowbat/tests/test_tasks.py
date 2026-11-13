import os
import shutil
import tempfile
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone as django_timezone

from olc_webportalv2.common.batch_client import BatchServiceError
from olc_webportalv2.cowbat.models import (
    AzureTask,
    SequencingRun,
    SummaryMetadata,
)
from olc_webportalv2.cowbat.tasks import (
    add_read_filenames_to_metadata,
    check_cowbat_progress,
    check_cowbat_tasks,
    check_sample_sheet_format,
    cowbat_api_submit,
    cowbat_cleanup,
    handle_incomplete_tasks,
    handle_task_completion,
    load_report,
    nextseq_run,
    no_sample_sheet,
    read_sections,
    run_cowbat_batch,
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

    @mock.patch(
        "olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True
    )
    @mock.patch("olc_webportalv2.cowbat.tasks.get_storage_client")
    def test_marks_run_upload_error_when_read_pair_is_incomplete(
        self, storage_client_factory, isdir
    ):
        run = self.create_run()
        storage_client_factory.return_value.list_objects.return_value = [
            mock.Mock(name="SEQ-1_R1.fastq"),
        ]
        storage_client_factory.return_value.list_objects.return_value[
            0
        ].name = "SEQ-1_R1.fastq"

        run_cowbat_batch.run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "UploadError")

    @mock.patch(
        "olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True
    )
    @mock.patch("olc_webportalv2.cowbat.tasks.get_storage_client")
    @mock.patch("olc_webportalv2.cowbat.tasks.nextseq_run")
    def test_nextseq_run_uses_nextseq_branch(
        self, nextseq_run, blob_service_class, isdir
    ):
        run = self.create_run(nextseq=True)
        blob_service_class.return_value.list_objects.return_value = [
            mock.Mock(name="SEQ-1_R1.fastq"),
            mock.Mock(name="SEQ-1_R2.fastq"),
        ]
        blob_service_class.return_value.list_objects.return_value[
            0
        ].name = "SEQ-1_R1.fastq"
        blob_service_class.return_value.list_objects.return_value[
            1
        ].name = "SEQ-1_R2.fastq"

        run_cowbat_batch.run(run.pk)

        self.assertEqual(nextseq_run.call_count, 1)
        self.assertEqual(
            nextseq_run.call_args[1]["container_name"], "191218-cal"
        )
        self.assertEqual(nextseq_run.call_args[1]["sequencing_run"].pk, run.pk)
        run.refresh_from_db()
        self.assertIsNotNone(run.task_started_at)
        self.assertEqual(run.task_queue, "cowbat")
        self.assertEqual(run.task_retry_count, 0)

    @mock.patch(
        "olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True
    )
    @mock.patch("olc_webportalv2.cowbat.tasks.get_storage_client")
    @mock.patch("olc_webportalv2.cowbat.tasks.submit_batch")
    def test_submits_complete_non_nextseq_run(
        self, submit_batch, blob_service_class, isdir
    ):
        run = self.create_run()
        blob_service_class.return_value.list_objects.return_value = [
            mock.Mock(name="SEQ-1_R1.fastq"),
            mock.Mock(name="SEQ-1_R2.fastq"),
        ]
        blob_service_class.return_value.list_objects.return_value[
            0
        ].name = "SEQ-1_R1.fastq"
        blob_service_class.return_value.list_objects.return_value[
            1
        ].name = "SEQ-1_R2.fastq"

        run_cowbat_batch.run(run.pk)

        self.assertEqual(submit_batch.call_count, 1)
        self.assertEqual(
            submit_batch.call_args[1]["sequencing_run"].pk, run.pk
        )
        self.assertEqual(
            submit_batch.call_args[1]["run_folder"],
            "olc_webportalv2/media/191218_CAL",
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
            blob_service_client=mock.Mock(),
            blob_files=["SEQ-1_R1.fastq.gz"],
            container_name=run.container,
            sequencing_run=run,
        )

        sub_run = SequencingRun.objects.get(run_name="260911-vl00772-1")
        self.assertEqual(sub_run.user_id, run.user_id)
        self.assertEqual(sub_run.status, "Processing")
        self.assertEqual(sub_run.workflow_state, "submitted")
        self.assertEqual(sub_run.task_submitted_at, submitted_at)
        self.assertIsNotNone(sub_run.task_started_at)
        self.assertEqual(sub_run.task_queue, "cowbat")
        self.assertEqual(sub_run.emails_array, ["operator@example.test"])
        self.assertEqual(cowbat_api_submit.call_count, 2)

    @mock.patch("olc_webportalv2.cowbat.tasks.copy_blobs")
    @mock.patch(
        "olc_webportalv2.cowbat.tasks.create_sub_container",
        side_effect=lambda blob_service_client, container_name, i: (
            f"{container_name}-{i}"
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
            blob_service_client=mock.Mock(),
            blob_files=blob_files,
            container_name="run-1",
            max_samples=1,
        )

        self.assertEqual(result, {1: ["SAMPLE-A"], 2: ["SAMPLE-B"]})
        self.assertEqual(create_sub_container.call_count, 2)
        self.assertEqual(copy_blobs.call_count, 2)
        self.assertEqual(
            copy_blobs.call_args_list[0][1]["sample_name"], "SAMPLE-A"
        )
        self.assertEqual(
            copy_blobs.call_args_list[1][1]["sample_name"], "SAMPLE-B"
        )

    @mock.patch("olc_webportalv2.cowbat.tasks.copy_blobs")
    @mock.patch(
        "olc_webportalv2.cowbat.tasks.create_sub_container",
        side_effect=lambda blob_service_client, container_name, i: (
            f"{container_name}-{i}"
        ),
    )
    def test_no_sample_sheet_balances_samples_across_subruns(
        self, create_sub_container, copy_blobs
    ):
        blob_files = [
            f"SAMPLE-{sample:03d}_R{read}.fastq.gz"
            for sample in range(96)
            for read in (1, 2)
        ]

        result = no_sample_sheet(
            blob_service_client=mock.Mock(),
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

    @mock.patch("olc_webportalv2.cowbat.tasks.generic_api_submit")
    def test_cowbat_api_submit_persists_batch_response(self, batch_submit):
        run = self.create_run()
        batch_submit.return_value = {
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
        batch_submit.assert_called_once_with(
            command="assemble",
            container_name=run.container,
            vm_size="Standard_D4s_v3",
            input_file_pattern=None,
            analysis_type="COWBAT",
            unique_id="FoodPort",
        )

    def test_check_sample_sheet_format_detects_target(self):
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False
        ) as sample_sheet:
            sample_sheet.write("[Header]\n[BCLConvert_Data]\n")
            path = sample_sheet.name

        try:
            self.assertTrue(check_sample_sheet_format(path))
            self.assertFalse(check_sample_sheet_format(path, "[Missing]"))
        finally:
            os.unlink(path)

    def test_read_sections_parses_simple_sections(self):
        content = "[Header]\nRunName,Run-1\n\n[Reads]\nRead1Cycles,151\n"
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False
        ) as sample_sheet:
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
        self.assertEqual(
            run.errors, ["Summary report not found in blob storage."]
        )

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
        values = ["SEQ-1", "Sample 1", "Escherichia"] + ["value"] * 18
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

            storage_client = mock.Mock()
            add_read_filenames_to_metadata(
                sequencing_run=run,
                blob_service_client=storage_client,
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
        self.assertEqual(storage_client.upload_file.call_count, 1)

    def test_add_read_filenames_ignores_missing_report(self):
        run = self.create_run()
        storage_client = mock.Mock()

        add_read_filenames_to_metadata(
            sequencing_run=run,
            blob_service_client=storage_client,
            container_name="run-1",
            reports_folder=tempfile.gettempdir(),
            blob_filenames=[],
        )

        storage_client.upload_file.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.errors, [])

    def test_add_read_filenames_preserves_existing_and_reports_ambiguous_reads(
        self,
    ):
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
                blob_service_client=mock.Mock(),
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

        self.assertIn(
            "existing_R1.fastq.gz,SEQ-1_R2.fastq.gz", report_contents
        )
        self.assertIn("SEQ-1_R1_a.fastq.gz", run.errors[0])
        self.assertIn("SEQ-1_R1_b.fastq.gz", run.errors[0])

    @mock.patch("olc_webportalv2.cowbat.tasks.cowbat_cleanup")
    def test_handle_task_completion_schedules_cleanup_for_success(
        self, cleanup
    ):
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
    def test_handle_task_completion_marks_run_error_for_failed_task(
        self, cleanup
    ):
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
    def test_handle_task_completion_records_azure_cleanup_errors(
        self, cleanup
    ):
        run = self.create_run(pool_id="pool-1", job_id="job-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [
            mock.Mock(execution_info=mock.Mock(exit_code=0)),
        ]
        batch_error = BatchServiceError(500, "Batch service failure")
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
        self.assertEqual(AzureTask.objects.filter(pk=azure_task.id).count(), 1)
        self.assertEqual(run.workflow_state, "ready")
        self.assertEqual(cleanup.apply_async.call_count, 0)

    @mock.patch("olc_webportalv2.cowbat.tasks.cowbat_cleanup")
    def test_handle_task_completion_keeps_tracker_if_cleanup_cannot_be_queued(
        self, cleanup
    ):
        run = self.create_run(pool_id="pool-1", job_id="job-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [
            mock.Mock(execution_info=mock.Mock(exit_code=0)),
        ]
        cleanup.apply_async.side_effect = RuntimeError("queue unavailable")

        handle_task_completion(
            batch_client=batch_client,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

        run.refresh_from_db()
        self.assertEqual(run.workflow_state, "ready")
        self.assertIn("Clean-up error:", run.errors)
        self.assertEqual(AzureTask.objects.filter(pk=azure_task.id).count(), 1)

    @mock.patch("olc_webportalv2.cowbat.tasks.cowbat_cleanup")
    @mock.patch("olc_webportalv2.cowbat.tasks.create_batch_client")
    def test_monitor_retries_cleanup_with_missing_batch_resources(
        self, batch_client_factory, cleanup
    ):
        run = self.create_run(
            pool_id="pool-1",
            job_id="job-1",
            status="Processing",
            workflow_state="ready",
            exit_code=0,
        )
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client = batch_client_factory.return_value
        batch_client.job.delete.side_effect = BatchServiceError(
            404,
            "job not found",
        )
        batch_client.pool.delete.side_effect = BatchServiceError(
            404,
            "pool not found",
        )

        check_cowbat_tasks()

        batch_client.task.list.assert_not_called()
        cleanup.apply_async.assert_called_once_with(
            queue="cowbat",
            args=(run.pk,),
        )
        self.assertEqual(AzureTask.objects.filter(pk=azure_task.id).count(), 0)

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

    @mock.patch(
        "olc_webportalv2.cowbat.tasks.retry_failed_pool_resize",
        return_value={
            "action": "non_retryable_resize_error",
            "resize_errors": [{"code": "quota"}],
        },
    )
    @mock.patch("olc_webportalv2.cowbat.tasks.check_cowbat_progress")
    def test_handle_incomplete_tasks_marks_resize_error(
        self, check_progress, retry_resize
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
        batch_client.job.delete.assert_called_once_with(job_id="job-1")
        batch_client.pool.delete.assert_called_once_with(pool_id="job-1")
        retry_resize.assert_called_once_with(
            batch_client=batch_client,
            pool_id="job-1",
        )
        check_progress.assert_not_called()

    @mock.patch(
        "olc_webportalv2.cowbat.tasks.retry_failed_pool_resize",
        return_value={"action": "resize_retried"},
    )
    @mock.patch("olc_webportalv2.cowbat.tasks.check_cowbat_progress")
    def test_handle_incomplete_tasks_retries_pool_resize(
        self, check_progress, retry_resize
    ):
        run = self.create_run(
            task_id="task-1", job_id="job-1", pool_id="job-1"
        )
        pool = mock.Mock(id="job-1", resize_errors=["quota"])
        batch_client = mock.Mock()
        batch_client.pool.list.return_value = [pool]

        handle_incomplete_tasks(
            batch_client=batch_client,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=1,
        )

        run.refresh_from_db()
        self.assertEqual(run.status, "Processing")
        self.assertEqual(run.workflow_state, "processing")
        self.assertEqual(run.task_retry_count, 1)
        retry_resize.assert_called_once_with(
            batch_client=batch_client,
            pool_id="job-1",
        )
        batch_client.job.delete.assert_not_called()
        batch_client.pool.delete.assert_not_called()
        check_progress.assert_not_called()

    def test_missing_resize_error_pool_finalizes_run_and_deletes_task(self):
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
        batch_client.job.delete.assert_called_once_with(job_id="job-1")
        batch_client.pool.delete.assert_called_once_with(pool_id="job-1")

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
        self.assertEqual(batch_client.file.list_from_task.call_count, 1)
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
    @mock.patch("olc_webportalv2.cowbat.tasks.create_batch_client")
    def test_check_cowbat_tasks_routes_completed_tasks(
        self, batch_client_class, handle_completion
    ):
        run = self.create_run(job_id="job-1")
        azure_task = AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client_class.return_value.task.list.return_value = [
            mock.Mock(state="completed"),
        ]

        check_cowbat_tasks()

        handle_completion.assert_called_once_with(
            batch_client=batch_client_class.return_value,
            sequencing_run=run,
            batch_job_name="job-1",
            azure_task_id=azure_task.id,
        )

    @mock.patch("olc_webportalv2.cowbat.tasks.handle_incomplete_tasks")
    @mock.patch("olc_webportalv2.cowbat.tasks.create_batch_client")
    def test_check_cowbat_tasks_routes_active_tasks(
        self, batch_client_class, handle_incomplete
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
    @mock.patch("olc_webportalv2.cowbat.tasks.create_batch_client")
    def test_check_cowbat_tasks_records_batch_error_without_processing_run(
        self,
        batch_client_class,
        handle_incomplete,
        handle_completion,
    ):
        run = self.create_run(job_id="job-1")
        AzureTask.objects.create(
            sequencing_run=run,
            exit_code_file="/tmp/exit_codes.txt",
        )
        batch_client_class.return_value.task.list.side_effect = (
            BatchServiceError(500, "Batch service failure")
        )

        check_cowbat_tasks()

        run.refresh_from_db()
        self.assertIn("Running task error:", run.errors)
        self.assertEqual(handle_incomplete.call_count, 0)
        self.assertEqual(handle_completion.call_count, 0)

    @mock.patch("olc_webportalv2.common.methods.smtplib.SMTP")
    @override_settings(ENVIRONMENT="PROD")
    def test_send_email_sends_message(self, smtp_class):
        server = smtp_class.return_value

        send_email(
            subject="Subject",
            body="Body",
            recipient="recipient@example.com",
        )

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
    @mock.patch(
        "olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True
    )
    @mock.patch("olc_webportalv2.cowbat.tasks.get_storage_client")
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
        blob_service_class.return_value.list_objects.return_value = [
            mock.Mock(name=blob_name) for blob_name in blob_names
        ]
        for blob, blob_name in zip(
            blob_service_class.return_value.list_objects.return_value,
            blob_names,
        ):
            blob.name = blob_name
        generate_link.return_value = "https://example.test/run.zip"

        cowbat_cleanup.run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "Complete")
        self.assertEqual(run.percent_complete, Decimal("100.00"))
        self.assertEqual(run.download_link, "https://example.test/run.zip")
        self.assertEqual(
            blob_service_class.return_value.download_file.call_count, 7
        )
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
    @mock.patch(
        "olc_webportalv2.cowbat.tasks.os.path.isfile", return_value=False
    )
    @mock.patch(
        "olc_webportalv2.cowbat.tasks.os.path.isdir", return_value=True
    )
    @mock.patch("olc_webportalv2.cowbat.tasks.get_storage_client")
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
        blob_service_class.return_value.list_objects.return_value = [blob]
        generate_link.return_value = "https://example.test/run.zip"

        cowbat_cleanup.run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, "Error")
        self.assertEqual(
            run.errors, ["Summary report not found in blob storage."]
        )
        self.assertEqual(send_email_mock.call_count, 0)
        self.assertEqual(make_archive.call_count, 1)
        self.assertEqual(generate_link.call_count, 1)
        self.assertEqual(rmtree.call_count, 1)
