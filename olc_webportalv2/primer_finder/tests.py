import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from django.http import HttpResponse
from django.test import RequestFactory

from olc_webportalv2.primer_finder.methods import create_blob_from_summary
from olc_webportalv2.primer_finder.forms import ValidatorTargetFormSetFactory
from olc_webportalv2.primer_finder.tasks import (
    _create_primer_cmd,
    _generate_input_blob_sas_urls,
)
from olc_webportalv2.primer_finder.validator_gene_reports import (
    filter_report_by_gene_categories,
    gene_exclusion_note,
)
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorRequest,
)
from olc_webportalv2.primer_finder.views import (
    primer_validator_home,
    primer_validator_processing,
    primer_validator_results,
    primer_verifier_home,
    primer_verifier_processing,
    primer_verifier_results,
)


class ValidatorHistoryViewTests(unittest.TestCase):
    @patch("olc_webportalv2.primer_finder.views.render")
    @patch(
        "olc_webportalv2.primer_finder.views.ValidatorRequest.objects.filter"
    )
    def test_home_lists_validator_requests(self, filter_requests, render):
        user = SimpleNamespace(is_authenticated=True)
        request = RequestFactory().get("/primer_finder/primer_validator/")
        request.user = user
        projects = [SimpleNamespace(pk=377)]
        filter_requests.return_value = projects
        render.return_value = HttpResponse()

        response = primer_validator_home(request)

        self.assertEqual(response.status_code, 200)
        filter_requests.assert_called_once_with(user=user)
        render.assert_called_once_with(
            request,
            "primer_finder/validator_home.html",
            {"validator_projects": projects},
        )

    @patch("olc_webportalv2.primer_finder.views.render")
    @patch("olc_webportalv2.primer_finder.views.get_object_or_404")
    def test_processing_passes_validator_request_to_template(
        self, get_object, render
    ):
        request = RequestFactory().get(
            "/primer_finder/primer_validator_processing/435/"
        )
        request.user = SimpleNamespace(is_authenticated=True)
        validator_request = SimpleNamespace(emails_array=[])
        get_object.return_value = validator_request
        render.return_value = HttpResponse()

        response = primer_validator_processing(request, validator_pk=435)

        self.assertEqual(response.status_code, 200)
        get_object.assert_called_once_with(ValidatorRequest, pk=435)
        args, kwargs = render.call_args
        self.assertEqual(
            args[:2],
            (request, "primer_finder/validator_processing.html"),
        )
        self.assertIs(
            args[2]["primer_validator_request"],
            validator_request,
        )
        self.assertIn("form", args[2])
        self.assertFalse(kwargs)

    @patch("olc_webportalv2.primer_finder.views.render")
    @patch("olc_webportalv2.primer_finder.views.get_object_or_404")
    @patch(
        "olc_webportalv2.primer_finder.views.ValidatorPrimerSet.objects.filter"
    )
    @patch(
        "olc_webportalv2.primer_finder.views.ValidatorPanel.objects.filter"
    )
    def test_results_render_validator_template_and_models(
        self,
        filter_panels,
        filter_primers,
        get_object,
        render,
    ):
        request = RequestFactory().get(
            "/primer_finder/primer_validator_results/435/"
        )
        request.user = SimpleNamespace(is_authenticated=True)
        validator_request = SimpleNamespace(pk=435, summary="{}")
        filter_panels.return_value = []
        filter_primers.return_value = []
        get_object.return_value = validator_request
        render.return_value = HttpResponse()

        response = primer_validator_results(request, validator_pk=435)

        self.assertEqual(response.status_code, 200)
        get_object.assert_called_once_with(ValidatorRequest, pk=435)
        filter_primers.assert_called_once_with(validator_request_id=435)
        filter_panels.assert_called_once_with(validator_request_id=435)
        args, kwargs = render.call_args
        self.assertEqual(
            args[:2],
            (request, "primer_finder/validator_results.html"),
        )
        self.assertIs(args[2]["primer_validator_request"], validator_request)
        self.assertNotIn("sample_results", args[2])
        self.assertFalse(kwargs)


class VerifierHistoryViewTests(unittest.TestCase):
    @patch("olc_webportalv2.primer_finder.views.render")
    @patch(
        "olc_webportalv2.primer_finder.views."
        "PrimerVerifierRequest.objects.filter"
    )
    def test_home_lists_verifier_requests(self, filter_requests, render):
        user = SimpleNamespace(is_authenticated=True)
        request = RequestFactory().get("/primer_finder/primer_verifier/")
        request.user = user
        projects = [SimpleNamespace(pk=484)]
        filter_requests.return_value = projects
        render.return_value = HttpResponse()

        response = primer_verifier_home(request)

        self.assertEqual(response.status_code, 200)
        filter_requests.assert_called_once_with(user=user)
        render.assert_called_once_with(
            request,
            "primer_finder/verifier_home.html",
            {"verifier_projects": projects},
        )

    @patch("olc_webportalv2.primer_finder.views.render")
    @patch("olc_webportalv2.primer_finder.views.get_object_or_404")
    def test_processing_uses_verifier_request(self, get_object, render):
        user = SimpleNamespace(is_authenticated=True)
        request = RequestFactory().get(
            "/primer_finder/primer_verifier_processing/484/"
        )
        request.user = user
        verifier_request = SimpleNamespace(emails_array=[])
        get_object.return_value = verifier_request
        render.return_value = HttpResponse()

        response = primer_verifier_processing(request, verifier_pk=484)

        self.assertEqual(response.status_code, 200)
        get_object.assert_called_once_with(PrimerVerifierRequest, pk=484)
        args, kwargs = render.call_args
        self.assertEqual(args[:2], (
            request,
            "primer_finder/verifier_processing.html",
        ))
        self.assertIs(args[2]["primer_verifier_request"], verifier_request)
        self.assertIn("form", args[2])
        self.assertFalse(kwargs)

    @patch("olc_webportalv2.primer_finder.views.VerifierPanel.objects.filter")
    @patch(
        "olc_webportalv2.primer_finder.views.VerifierPrimerSet.objects.filter"
    )
    @patch("olc_webportalv2.primer_finder.views.get_object_or_404")
    @patch("olc_webportalv2.primer_finder.views.render")
    def test_results_use_verifier_child_models(
        self, render, get_object, filter_primers, filter_panels
    ):
        user = SimpleNamespace(is_authenticated=True)
        request = RequestFactory().get(
            "/primer_finder/primer_verifier_results/484/"
        )
        request.user = user
        verifier_request = SimpleNamespace(pk=484, summary="{}")
        primer = SimpleNamespace(primer_name="primer-a")
        panel = SimpleNamespace(panel="inclusivity", genus="Example")
        get_object.return_value = verifier_request
        filter_primers.return_value = [primer]
        filter_panels.return_value = [panel]
        render.return_value = HttpResponse()

        response = primer_verifier_results(request, verifier_pk=484)

        self.assertEqual(response.status_code, 200)
        get_object.assert_called_once_with(PrimerVerifierRequest, pk=484)
        filter_primers.assert_called_once_with(verifier_request_id=484)
        filter_panels.assert_called_once_with(verifier_request_id=484)
        self.assertEqual(
            render.call_args.args[1],
            "primer_finder/verifier_results.html",
        )


class PrimerFinderStorageTests(unittest.TestCase):
    def test_primer_command_includes_probe_and_copies_reports(self):
        request = SimpleNamespace(
            probe_sequence='ACGT',
            mismatches=2,
            min_amplicon_size=0,
            max_amplicon_size=1500,
            range_buffer=0,
            contig_breaks=False,
        )

        command = _create_primer_cmd(
            container_name='primer-run',
            file_name='primers.fasta',
            input_blobs=[],
            path='$AZ_BATCH_NODE_MOUNTS_DIR/primer-run',
            request=request,
        )

        self.assertIn('-p /datadrive/primer-run/probe.fasta', command)
        self.assertIn(
            'cp -R /datadrive/primer-run/reports "$mount_dir/"',
            command,
        )

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


class ValidatorGeneReportTests(unittest.TestCase):
    def test_selected_gene_categories_filter_summary_strains(self):
        report = {
            'target': {
                'inclusivity': {
                    'strain-with-frame-shift': {'amplicon_length': 100},
                    'strain-retained': {'amplicon_length': 120},
                }
            }
        }

        filtered = filter_report_by_gene_categories(
            json.dumps(report),
            {'strain-with-frame-shift': ['FRAME_SHIFT']},
            {'FRAME_SHIFT'},
        )

        self.assertEqual(
            json.loads(filtered),
            {
                'target': {
                    'inclusivity': {
                        'strain-retained': {'amplicon_length': 120},
                    }
                }
            },
        )

    def test_exclusion_note_only_mentions_applied_exclusions(self):
        self.assertEqual(
            gene_exclusion_note(
                {'FRAME_SHIFT'},
                {'strain-a': ['FRAME_SHIFT'], 'strain-b': ['EXTENDED']},
            ),
            'Gene-analysis exclusions: FRAME_SHIFT category selected; '
            '1 strain removed from summary totals and model results. '
            'Categorized strains remain in the Excel report.',
        )
        self.assertEqual(
            gene_exclusion_note({'FRAME_SHIFT'}, {}),
            '',
        )

    def test_salmonella_rule_requires_marker_per_sample(self):
        data = {
            'targets-TOTAL_FORMS': '2',
            'targets-INITIAL_FORMS': '0',
            'targets-MIN_NUM_FORMS': '1',
            'targets-MAX_NUM_FORMS': '1000',
        }
        for index, (sample, target) in enumerate((
            ('sample-a', 'invA'),
            ('sample-b', 'other'),
        )):
            data.update({
                'targets-{0}-sample_name'.format(index): sample,
                'targets-{0}-target_name'.format(index): target,
                'targets-{0}-forward_primer'.format(index): 'ACGTACGT',
                'targets-{0}-reverse_primer'.format(index): 'TGCATGCA',
                'targets-{0}-probe_sequence'.format(index): '',
                'targets-{0}-probe_evaluation_method'.format(index):
                    'percent_identity',
                'targets-{0}-probe_percent_identity'.format(index): '80',
                'targets-{0}-probe_max_differences'.format(index): '2',
                'targets-{0}-gene_sequence'.format(index): '',
            })
        formset = ValidatorTargetFormSetFactory(data, prefix='targets')
        formset.classification_rule = 'salmonella'
        formset.logic = ''

        self.assertFalse(formset.is_valid())
        self.assertIn(
            'Each Salmonella sample requires an invA or stn target.',
            str(formset.non_form_errors()),
        )
