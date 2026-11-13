import os
import tempfile
from datetime import date
from unittest import mock

from django.test import TestCase

from olc_webportalv2.users.models import User
from olc_webportalv2.vir_typer.models import (
    VirTyperFiles,
    VirTyperProject,
    VirTyperRequest,
)
from olc_webportalv2.vir_typer.tasks import make_config_file, run_vir_typer


class VirTyperTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="vir-typer-task-test")
        self.project = VirTyperProject.objects.create(
            user=self.user,
            project_name="Task Project",
        )

    def test_make_config_file_writes_sequences_and_output_directory(self):
        with tempfile.NamedTemporaryFile(delete=False) as config_file:
            config_path = config_file.name

        try:
            make_config_file(
                job_name="vir-typer-1-task-project",
                sequences=["sample_R1.fasta", "sample_R2.fasta"],
                input_data_folder="sequences",
                output_data_folder="reports",
                command="run-analysis",
                config_file=config_path,
            )
            with open(config_path, "r") as generated:
                contents = generated.read()
        finally:
            os.unlink(config_path)

        self.assertIn(
            "CLOUDIN:=vir-typer-1-task-project/sample_R1.fasta ", contents
        )
        self.assertIn(
            "vir-typer-1-task-project/sample_R2.fasta sequences", contents
        )
        self.assertIn("OUTPUT:=reports/", contents)
        self.assertIn("COMMAND:=run-analysis", contents)

    def test_run_task_submits_and_marks_project_processing(self):
        request = VirTyperRequest.objects.create(
            project_name=self.project,
            isolate_source="source",
            LSTS_ID="LSTS-1",
            sample_name="sample-1",
            date_received=date(2024, 1, 1),
            analyst_name="analyst",
        )
        VirTyperFiles.objects.create(
            sample_name=request,
            sequence_file="sample_R1.fasta",
        )

        with mock.patch(
            "olc_webportalv2.vir_typer.tasks.generic_api_submit"
        ) as submit:
            run_vir_typer.run(self.project.pk)

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "Processing")
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(self.project.project_azure_request.count(), 1)

    @mock.patch("olc_webportalv2.vir_typer.tasks.capture_exception")
    @mock.patch(
        "olc_webportalv2.vir_typer.tasks.generic_api_submit",
        side_effect=RuntimeError("submission failed"),
    )
    def test_run_task_marks_project_error_when_submission_fails(
        self, submit, capture_exception
    ):
        run_vir_typer.run(self.project.pk)

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "Error")
        self.assertEqual(capture_exception.call_count, 1)
        self.assertEqual(submit.call_count, 1)
