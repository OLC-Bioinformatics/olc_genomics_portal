import re

from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorPanel,
    ValidatorRequest,
    ValidatorPrimerSet,
    VerifierPanel,
    VerifierPrimerSet,
)
from olc_webportalv2.users.models import User


class PrimerValidatorUiActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="primer-validator-ui",
            password="password",
        )
        self.client.login(
            username="primer-validator-ui",
            password="password",
        )

    def test_status_and_download_buttons_have_the_expected_targets(self):
        complete = ValidatorRequest.objects.create(
            user=self.user,
            project_name="complete-project",
            status="Complete",
            report_download_link="/reports/complete.zip",
        )
        processing = ValidatorRequest.objects.create(
            user=self.user,
            project_name="processing-project",
            status="Processing",
        )
        error = ValidatorRequest.objects.create(
            user=self.user,
            project_name="error-project",
            status="Error",
        )
        unprocessed = ValidatorRequest.objects.create(
            user=self.user,
            project_name="unprocessed-project",
            status="Unprocessed",
        )

        response = self.client.get(
            reverse("primer_finder:primer_validator_home")
        )

        self.assertEqual(response.status_code, 200)
        processing_url = reverse(
            "primer_finder:primer_validator_processing",
            kwargs={"validator_pk": complete.pk},
        )
        self.assertContains(response, f'href="{processing_url}"')
        self.assertContains(response, 'href="/reports/complete.zip"')
        self.assertContains(
            response,
            f'id="row{processing.pk}"',
        )
        self.assertContains(response, "disabled> Processing")
        self.assertContains(
            response,
            'disabled> Unprocessed </button>',
        )
        self.assertNotContains(
            response,
            f'id="form{processing.pk}"',
        )
        self.assertContains(
            response,
            f'id="form{unprocessed.pk}"',
        )
        self.assertContains(
            response,
            'href="{0}"'.format(
                reverse(
                    "primer_finder:primer_validator_processing",
                    kwargs={"validator_pk": error.pk},
                )
            ),
        )

    def test_delete_button_submits_its_project_and_cannot_delete_others(self):
        own_request = ValidatorRequest.objects.create(
            user=self.user,
            project_name="own-project",
            status="Complete",
        )
        other_user = User.objects.create_user(username="other-primer-user")
        other_request = ValidatorRequest.objects.create(
            user=other_user,
            project_name="other-project",
            status="Complete",
        )

        response = self.client.get(
            reverse("primer_finder:primer_validator_home")
        )
        html = response.content.decode(response.charset)
        form_match = re.search(
            rf'<form id="form{own_request.pk}" method="post">(.*?)</form>',
            html,
            re.DOTALL,
        )

        self.assertIsNotNone(form_match)
        self.assertRegex(
            form_match.group(1),
            rf'name="delete"\s+value="{own_request.pk}"',
        )
        self.assertNotIn("other-project", html)

        response = self.client.post(
            reverse("primer_finder:primer_validator_home"),
            {"delete": other_request.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ValidatorRequest.objects.filter(pk=other_request.pk).exists()
        )

        self.client.post(
            reverse("primer_finder:primer_validator_home"),
            {"delete": own_request.pk},
        )
        self.assertFalse(
            ValidatorRequest.objects.filter(pk=own_request.pk).exists()
        )


class PrimerFinderLandingUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="primer-finder-landing-ui",
            password="password",
        )
        self.client.login(
            username="primer-finder-landing-ui",
            password="password",
        )

    def test_landing_cards_and_create_buttons_link_to_their_pages(self):
        response = self.client.get(reverse("primer_finder:primer_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'href="{0}"'.format(
                reverse("primer_finder:primer_validator_home")
            ),
        )
        self.assertContains(
            response,
            'href="{0}"'.format(
                reverse("primer_finder:primer_verifier_home")
            ),
        )

        pages = (
            ("primer_finder:primer_validator_home",
             "primer_finder:primer_validator_request"),
            ("primer_finder:primer_verifier_home",
             "primer_finder:primer_verifier_request"),
        )
        for home_route, create_route in pages:
            response = self.client.get(reverse(home_route))
            self.assertEqual(response.status_code, 200)
            self.assertContains(
                response,
                f'href="{reverse(create_route)}"',
            )

    def test_create_forms_render_fields_and_submit_buttons(self):
        validator_response = self.client.get(
            reverse("primer_finder:primer_validator_request")
        )
        self.assertEqual(validator_response.status_code, 200)
        self.assertContains(validator_response, 'id="target-forms"')
        self.assertContains(validator_response, 'id="add-target"')
        self.assertContains(
            validator_response,
            'name="targets-0-forward_primer"',
        )
        self.assertContains(validator_response, "Run PrimerValidator")

        verifier_response = self.client.get(
            reverse("primer_finder:primer_verifier_request")
        )
        self.assertEqual(verifier_response.status_code, 200)
        self.assertContains(verifier_response, 'name="primer_sequences"')
        self.assertContains(verifier_response, 'name="probe_sequence"')
        self.assertContains(verifier_response, "Run PrimerVerifier")


class PrimerVerifierUiActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="primer-verifier-ui",
            password="password",
        )
        self.client.login(
            username="primer-verifier-ui",
            password="password",
        )

    def test_dashboard_status_download_and_delete_controls_are_wired(self):
        complete = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name="verifier-complete",
            status="Complete",
            report_download_link="/reports/verifier.zip",
        )
        processing = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name="verifier-processing",
            status="Processing",
        )
        error = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name="verifier-error",
            status="Error",
        )
        unprocessed = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name="verifier-unprocessed",
            status="Unprocessed",
        )

        response = self.client.get(
            reverse("primer_finder:primer_verifier_home")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/reports/verifier.zip"')
        self.assertContains(response, f'id="row{complete.pk}"')
        self.assertContains(
            response,
            'href="{0}"'.format(
                reverse(
                    "primer_finder:primer_verifier_processing",
                    kwargs={"verifier_pk": complete.pk},
                )
            ),
        )
        self.assertContains(
            response,
            'href="{0}"'.format(
                reverse(
                    "primer_finder:primer_verifier_processing",
                    kwargs={"verifier_pk": error.pk},
                )
            ),
        )
        self.assertContains(response, "disabled> Processing")
        self.assertContains(response, "disabled> Unprocessed")
        self.assertNotContains(response, f'id="form{processing.pk}"')
        self.assertContains(response, f'id="form{unprocessed.pk}"')

    def test_delete_form_contains_hidden_id_and_deletes_selected_request(self):
        own_request = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name="own-verifier",
            status="Complete",
        )
        other_user = User.objects.create_user(username="other-verifier-ui")
        other_request = PrimerVerifierRequest.objects.create(
            user=other_user,
            project_name="foreign-verifier",
            status="Complete",
        )
        response = self.client.get(
            reverse("primer_finder:primer_verifier_home")
        )
        html = response.content.decode(response.charset)
        form_match = re.search(
            rf'<form id="form{own_request.pk}" method="post">(.*?)</form>',
            html,
            re.DOTALL,
        )

        self.assertIsNotNone(form_match)
        self.assertRegex(
            form_match.group(1),
            rf'name="delete"\s+value="{own_request.pk}"',
        )
        self.assertNotIn("foreign-verifier", html)

        self.client.post(
            reverse("primer_finder:primer_verifier_home"),
            {"delete": other_request.pk},
        )
        self.assertTrue(
            PrimerVerifierRequest.objects.filter(pk=other_request.pk).exists()
        )
        self.client.post(
            reverse("primer_finder:primer_verifier_home"),
            {"delete": own_request.pk},
        )
        self.assertFalse(
            PrimerVerifierRequest.objects.filter(pk=own_request.pk).exists()
        )

    def test_complete_processing_page_links_to_reports_and_results(self):
        request = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name="verifier-results",
            status="Complete",
            report_download_link="/reports/verifier.zip",
            summary_download_link="/reports/verifier.xlsx",
        )

        response = self.client.get(reverse(
            "primer_finder:primer_verifier_processing",
            kwargs={"verifier_pk": request.pk},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/reports/verifier.zip"')
        self.assertContains(response, 'href="/reports/verifier.xlsx"')
        self.assertContains(
            response,
            'href="{0}"'.format(
                reverse(
                    "primer_finder:primer_verifier_results",
                    kwargs={"verifier_pk": request.pk},
                )
            ),
        )

    def test_results_page_displays_summary_and_matching_detail_controls(self):
        request = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name="verifier-summary",
            summary=(
                '{"invA":{"inclusivity":{"combined":{"forward_mismatches":[]},'
                '"listeria":{"forward_details":[["1:A>G",1]],'
                '"positive":1,"total":1,"percent":100}}}}'
            ),
        )
        VerifierPrimerSet.objects.create(
            verifier_request=request,
            primer_name="invA",
        )
        VerifierPanel.objects.create(
            verifier_request=request,
            panel="inclusivity",
            genus="Listeria",
        )

        response = self.client.get(reverse(
            "primer_finder:primer_verifier_results",
            kwargs={"verifier_pk": request.pk},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Summary Report for verifier-summary")
        self.assertContains(response, "100")
        self.assertContains(
            response,
            'data-primer-detail-toggle="true" data-primer="invA"',
        )
        self.assertContains(response, 'data-panel="inclusivity"')
        self.assertContains(response, 'id="invAinclusivitylisteriaRow"')
        self.assertContains(response, 'id="invAinclusivityModal"')


class PrimerValidatorResultsUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="primer-validator-results-ui",
            password="password",
        )
        self.client.login(
            username="primer-validator-results-ui",
            password="password",
        )

    def test_results_page_renders_summary_and_detail_toggle_targets(self):
        request = ValidatorRequest.objects.create(
            user=self.user,
            project_name="validator-summary",
            summary=(
                '{"invA":{"inclusivity":{"combined":{"forward_details":[]},'
                '"listeria":{"forward_details":[["1:A>G",1]],'
                '"positive":1,"total":1,"percent":100}}}}'
            ),
            forward_primer="ACGT",
            reverse_primer="TGCA",
        )
        ValidatorPrimerSet.objects.create(
            validator_request=request,
            primer_name="invA",
        )
        ValidatorPanel.objects.create(
            validator_request=request,
            panel="inclusivity",
            genus="Listeria",
        )

        response = self.client.get(reverse(
            "primer_finder:primer_validator_results",
            kwargs={"validator_pk": request.pk},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "validator-summary")
        self.assertContains(response, "100")
        self.assertContains(
            response,
            'data-primer-detail-toggle="true" data-primer="invA"',
        )
        self.assertContains(response, 'data-panel="inclusivity"')
        self.assertContains(response, 'id="invAinclusivitylisteriaRow"')
        self.assertContains(response, 'id="invAinclusivityModal"')

    def test_processing_page_shows_email_form_and_home_link(self):
        request = ValidatorRequest.objects.create(
            user=self.user,
            project_name="validator-processing",
            status="Processing",
            forward_primer="ACGT",
            reverse_primer="TGCA",
            emails_array=["existing@example.org"],
        )

        response = self.client.get(reverse(
            "primer_finder:primer_validator_processing",
            kwargs={"validator_pk": request.pk},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id='addEmail'")
        self.assertContains(response, 'name="email"')
        self.assertContains(response, "existing@example.org")
        self.assertContains(
            response,
            'href="{0}"'.format(
                reverse("primer_finder:primer_validator_home")
            ),
        )
