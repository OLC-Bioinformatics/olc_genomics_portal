from types import SimpleNamespace
import shlex
import unittest
from unittest.mock import Mock, call, patch

from django.urls import resolve, reverse
from django.template.loader import render_to_string

from olc_webportalv2.primer_finder import tasks
from olc_webportalv2.primer_finder.forms import PrimerValidatorForm
from olc_webportalv2.primer_finder.methods import create_blob_from_summary
from olc_webportalv2.primer_finder.tasks import (
    _create_primer_cmd,
    _generate_input_blob_sas_urls,
)


class PrimerFinderStorageTests(unittest.TestCase):
    @patch("olc_webportalv2.primer_finder.tasks.get_storage_client")
    def test_input_urls_are_deduplicated_and_expiring(self, get_client):
        client = get_client.return_value
        client.object_exists.return_value = True
        client.create_transfer_url.side_effect = ["first-url", "second-url"]

        result = _generate_input_blob_sas_urls(
            container_name="primer-run",
            blob_names=["panel.json", "primers.csv", "panel.json"],
        )

        self.assertEqual(
            result,
            [
                {"blob_name": "panel.json", "sas_url": "first-url"},
                {"blob_name": "primers.csv", "sas_url": "second-url"},
            ],
        )
        self.assertEqual(
            client.object_exists.call_args_list,
            [
                call("primer-run", "panel.json"),
                call("primer-run", "primers.csv"),
            ],
        )
        self.assertEqual(
            client.create_transfer_url.call_args_list,
            [
                call(
                    "primer-run",
                    "download",
                    name="panel.json",
                    expires_in_seconds=12 * 60 * 60,
                ),
                call(
                    "primer-run",
                    "download",
                    name="primers.csv",
                    expires_in_seconds=12 * 60 * 60,
                ),
            ],
        )

    @patch("olc_webportalv2.primer_finder.tasks.get_storage_client")
    def test_input_url_generation_rejects_missing_blob(self, get_client):
        client = get_client.return_value
        client.object_exists.return_value = False

        with self.assertRaisesRegex(
            FileNotFoundError, "primer-run/missing.json"
        ):
            _generate_input_blob_sas_urls(
                container_name="primer-run",
                blob_names=["missing.json"],
            )

        client.create_transfer_url.assert_not_called()

    @patch("olc_webportalv2.primer_finder.methods.get_storage_client")
    def test_summary_is_uploaded_through_storage_client(self, get_client):
        request = SimpleNamespace(
            container_namer=Mock(return_value="primer-run"),
            summary='{"status": "complete"}',
        )

        create_blob_from_summary(request=request)

        get_client.return_value.upload_bytes.assert_called_once_with(
            "primer-run",
            "reports/primer-run_summary_report.json",
            b'{"status": "complete"}',
        )


class PrimerValidatorWorkflowTests(unittest.TestCase):
    def test_empty_form_returns_required_field_errors(self):
        form = PrimerValidatorForm(data={})

        self.assertFalse(form.is_valid())
        self.assertIn("forward_primer", form.errors)
        self.assertIn("reverse_primer", form.errors)

    def test_validator_routes_resolve_to_validator_views(self):
        self.assertEqual(
            resolve(reverse("primer_finder:primer_validator_home")).func.__name__,
            "primer_validator_home",
        )
        self.assertEqual(
            resolve(reverse("primer_finder:primer_validator_request")).func.__name__,
            "primer_validator_request",
        )
        self.assertEqual(
            resolve(
                reverse(
                    "primer_finder:primer_validator_processing",
                    kwargs={"validator_pk": 1},
                )
            ).func.__name__,
            "primer_validator_processing",
        )
        self.assertEqual(
            resolve(
                reverse(
                    "primer_finder:primer_validator_results",
                    kwargs={"validator_pk": 1},
                )
            ).func.__name__,
            "primer_validator_results",
        )

    def test_processing_templates_render_their_own_result_routes(self):
        validator_request = SimpleNamespace(
            status="Complete",
            pk=42,
            report_download_link="/validator-report",
            summary_download_link="/validator-summary",
            emails_array=[],
        )
        verifier_request = SimpleNamespace(
            status="Complete",
            pk=43,
            report_download_link="/verifier-report",
            summary_download_link="/verifier-summary",
            emails_array=[],
        )

        validator_html = render_to_string(
            "primer_finder/validator_processing.html",
            {"primer_validator_request": validator_request},
        )
        verifier_html = render_to_string(
            "primer_finder/verifier_processing.html",
            {"primer_verifier_request": verifier_request},
        )

        self.assertIn(
            reverse(
                "primer_finder:primer_validator_results",
                kwargs={"validator_pk": validator_request.pk},
            ),
            validator_html,
        )
        self.assertIn(
            reverse(
                "primer_finder:primer_validator_report",
                kwargs={"validator_pk": validator_request.pk},
            ),
            validator_html,
        )
        self.assertIn(
            reverse(
                "primer_finder:primer_verifier_results",
                kwargs={"verifier_pk": verifier_request.pk},
            ),
            verifier_html,
        )

    def test_batch_command_runs_validator_and_copies_outputs(self):
        sas_url = "https://storage.example/primers.fasta?sig=a&sp=r"
        request = SimpleNamespace(
            mismatches=2,
            min_amplicon_size=0,
            max_amplicon_size=1500,
            range_buffer=0,
            contig_breaks=False,
            probe_sequence="ACGT",
        )

        command = _create_primer_cmd(
            container_name="primer-run",
            file_name="primers.fasta",
            input_blobs=[
                {"blob_name": "primers.fasta", "sas_url": sas_url},
                {
                    "blob_name": "probe.fasta",
                    "sas_url": "https://storage.example/probe.fasta?sig=b",
                },
            ],
            path="$AZ_BATCH_NODE_MOUNTS_DIR/primer-run",
            request=request,
        )

        self.assertIn(shlex.quote(sas_url), command)
        self.assertIn("primer_validator.py", command)
        self.assertIn("-p /datadrive/primer-run/probe.fasta", command)
        self.assertIn(
            'cp -R /datadrive/primer-run/reports "$mount_dir/"', command
        )
        self.assertIn("for seqids_file in", command)

    def test_validator_task_prepares_inputs_before_building_batch_command(self):
        primer_request = SimpleNamespace(probe_sequence=None)
        sas_inputs = [
            {"blob_name": "primers.fasta", "sas_url": "primer-url"},
            {"blob_name": "inclusivity.zip", "sas_url": "inclusivity-url"},
            {"blob_name": "exclusivity.zip", "sas_url": "exclusivity-url"},
        ]

        with (
            patch.object(
                tasks.ValidatorRequest.objects, "get", return_value=primer_request
            ),
            patch.object(
                tasks, "upload_primers", return_value=("primer-run", "primers.fasta")
            ),
            patch.object(
                tasks,
                "_handle_panel_files",
                side_effect=[["inclusivity.zip"], ["exclusivity.zip"]],
            ) as handle_panel_files,
            patch.object(
                tasks, "_generate_input_blob_sas_urls", return_value=sas_inputs
            ) as generate_urls,
            patch.object(
                tasks, "_create_primer_cmd", return_value="batch command"
            ) as create_command,
            patch.object(tasks, "generic_api_submit") as submit,
            patch.object(tasks.PrimerValidatorAzureRequest.objects, "create") as track,
        ):
            tasks._process_primer_request(
                panel_model=tasks.ValidatorPanel,
                request_model=tasks.ValidatorRequest,
                request_pk=42,
            )

        self.assertEqual(handle_panel_files.call_count, 2)
        generate_urls.assert_called_once_with(
            container_name="primer-run",
            blob_names=[
                "primers.fasta",
                "inclusivity.zip",
                "exclusivity.zip",
            ],
        )
        create_command.assert_called_once_with(
            container_name="primer-run",
            file_name="primers.fasta",
            input_blobs=sas_inputs,
            path="$AZ_BATCH_NODE_MOUNTS_DIR/primer-run",
            request=primer_request,
        )
        submit.assert_called_once()
        self.assertEqual(submit.call_args.kwargs["command"], "batch command")
        track.assert_called_once_with(
            validator_request=primer_request,
            exit_code_file="NA",
        )
