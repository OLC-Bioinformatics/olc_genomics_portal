"""Unit tests for COWSNPhR forms, storage, and processing workflows."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile

from olc_webportalv2.cowsnphr.forms import (
    COWSNPhrForm,
    COWSNPhrSEQIDForm,
)
from olc_webportalv2.cowsnphr.models import (
    COWSNPhRAzureTask,
    COWSNPhRRequest,
)
from olc_webportalv2.cowsnphr.tasks import (
    check_for_task_completion,
    compress_outputs,
    container_sanity,
    find_containers,
    read_spreadsheet,
    upload_files_for_cowsnphr,
)
from olc_webportalv2.cowsnphr.views import cowsnphr_processing


class COWSNPhRStorageTests(unittest.TestCase):
    @patch("olc_webportalv2.cowsnphr.tasks.get_storage_client")
    def test_find_containers_excludes_output_containers(self, get_client):
        client = get_client.return_value
        client.list_containers.return_value = [
            SimpleNamespace(name="123456-run"),
            SimpleNamespace(name="cowsnphr-run"),
            SimpleNamespace(name="123456-run-output"),
            SimpleNamespace(name="unrelated"),
        ]

        self.assertEqual(
            find_containers(),
            ["123456-run", "cowsnphr-run"],
        )

    @patch("olc_webportalv2.cowsnphr.tasks.get_storage_client")
    def test_container_sanity_checks_required_paths(self, get_client):
        client = get_client.return_value
        client.list_objects.return_value = [
            SimpleNamespace(name="fastq/sample.fastq.gz"),
            SimpleNamespace(name="ref/reference.fasta"),
            SimpleNamespace(name="ref/regions.bed"),
        ]

        self.assertEqual(
            container_sanity("cowsnphr-run"),
            (True, True, "ref/regions.bed"),
        )
        client.list_objects.assert_called_once_with("cowsnphr-run")

    @patch("olc_webportalv2.cowsnphr.tasks.get_storage_client")
    def test_upload_files_routes_fastq_to_fastq_folder(self, get_client):
        client = get_client.return_value
        uploaded_file = SimpleUploadedFile(
            "sample.fastq.gz",
            b"reads",
            content_type="application/gzip",
        )

        upload_files_for_cowsnphr(uploaded_file, "cowsnphr-run")

        client.ensure_container.assert_called_once_with("cowsnphr-run")
        client.upload_bytes.assert_called_once_with(
            "cowsnphr-run",
            "fastq/sample.fastq.gz",
            b"reads",
            content_type="application/gzip",
        )

    @patch("olc_webportalv2.cowsnphr.tasks.COWSNPhRRequest.objects.get")
    def test_missing_batch_tasks_are_not_reported_complete(self, get_request):
        cowsnphr_request = COWSNPhRRequest(
            pk=1,
            container_name="cowsnphr-run",
        )
        task = COWSNPhRAzureTask(
            cowsnphr=cowsnphr_request,
            exit_code_file="",
        )
        get_request.return_value = cowsnphr_request
        batch_client = Mock()
        batch_client.task.list.return_value = []

        self.assertEqual(
            check_for_task_completion(task, batch_client),
            (False, cowsnphr_request),
        )
        batch_client.pool.list.assert_not_called()

    def test_read_spreadsheet_loads_excel_reports(self):
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.xlsx"
            pd.DataFrame(
                {"gene": ["gene-A"], "sample": ["sample-A"]}
            ).to_excel(report_path, index=False)

            report, headers, padding = read_spreadsheet(
                str(report_path),
                excel=True,
            )

        self.assertEqual(headers, ["gene", "sample"])
        self.assertEqual(report, {0: ["gene-A", "sample-A"]})
        self.assertEqual(padding, [])

    def test_invalid_container_name_does_not_break_form_cleaning(self):
        form = COWSNPhrForm(data={"container_name": "x" * 65})

        self.assertFalse(form.is_valid())
        self.assertIn("container_name", form.errors)

    def test_seqid_form_keeps_all_supplied_ids(self):
        form = COWSNPhrSEQIDForm(
            data={
                "project_name": "",
                "seqids": ["SEQID-1", "SEQID-2"],
                "ref": "REFERENCE-1",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["seqids"], ["SEQID-1", "SEQID-2"])

    @patch("olc_webportalv2.cowsnphr.views.messages.success")
    @patch("olc_webportalv2.cowsnphr.views.render")
    @patch("olc_webportalv2.cowsnphr.views.EmailForm")
    @patch("olc_webportalv2.cowsnphr.views.get_object_or_404")
    def test_processing_view_accepts_email_with_null_email_list(
        self, get_request, email_form, render, success_message
    ):
        cowsnphr_request = SimpleNamespace(
            status="Complete",
            emails_array=None,
            save=Mock(),
        )
        get_request.return_value = cowsnphr_request
        valid_form = Mock()
        valid_form.is_valid.return_value = True
        valid_form.cleaned_data = {"email": "user@example.com"}
        email_form.side_effect = [Mock(), valid_form, Mock()]
        request = SimpleNamespace(
            method="POST",
            POST={"email": "user@example.com"},
            user=SimpleNamespace(is_authenticated=True),
        )

        cowsnphr_processing(request, cowsnphr_pk=1)

        self.assertEqual(
            cowsnphr_request.emails_array,
            ["user@example.com"],
        )
        cowsnphr_request.save.assert_called_once()
        success_message.assert_called_once()
        render.assert_called_once()

    @patch("olc_webportalv2.cowsnphr.tasks.os.makedirs")
    @patch(
        "olc_webportalv2.cowsnphr.tasks.os.remove",
        side_effect=FileNotFoundError,
    )
    @patch("olc_webportalv2.cowsnphr.tasks.shutil.make_archive")
    def test_compress_outputs_creates_archive_directory(
        self, make_archive, _, makedirs
    ):
        cowsnphr = SimpleNamespace(project_name="project", pk=7)

        archive_path = compress_outputs(cowsnphr, "report-folder")

        makedirs.assert_called_once_with(
            "olc_webportalv2/media/cowsnphr_archives",
            exist_ok=True,
        )
        make_archive.assert_called_once_with(
            "olc_webportalv2/media/cowsnphr_archives/project_7",
            "zip",
            "report-folder",
        )
        self.assertEqual(
            archive_path,
            "olc_webportalv2/media/cowsnphr_archives/project_7.zip",
        )
