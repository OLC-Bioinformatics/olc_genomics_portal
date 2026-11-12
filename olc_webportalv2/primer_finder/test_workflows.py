from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorPanel,
    ValidatorPrimerSet,
    ValidatorRequest,
    ValidatorSample,
    ValidatorTarget,
    VerifierPanel,
)
from olc_webportalv2.users.models import User


class PrimerFinderWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="primer-finder-workflow",
            password="password",
        )
        self.client.login(
            username="primer-finder-workflow",
            password="password",
        )

    def validator_post_data(self, **overrides):
        data = {
            "classification_rule": "none",
            "logic": "",
            "inclusivity_panel": ["listeria"],
            "targets-TOTAL_FORMS": "1",
            "targets-INITIAL_FORMS": "0",
            "targets-MIN_NUM_FORMS": "1",
            "targets-MAX_NUM_FORMS": "1000",
            "targets-0-sample_name": "Sample 1",
            "targets-0-target_name": "invA",
            "targets-0-forward_primer": "ACGTACGT",
            "targets-0-reverse_primer": "TGCATGCA",
        }
        data.update(overrides)
        return data

    def test_validator_submission_persists_targets_and_queues_analysis(self):
        with patch(
            "olc_webportalv2.primer_finder.views.run_primer_validator.apply_async"
        ) as apply_async:
            response = self.client.post(
                reverse("primer_finder:primer_validator_request"),
                self.validator_post_data(),
            )

        self.assertEqual(response.status_code, 302)
        validator_request = ValidatorRequest.objects.get()
        self.assertEqual(validator_request.user, self.user)
        self.assertEqual(validator_request.status, "Processing")
        self.assertTrue(validator_request.project_name)
        self.assertEqual(validator_request.primer_sequences, (
            ">invA-F\nACGTACGT\n>invA-R\nTGCATGCA"
        ))
        self.assertEqual(
            list(ValidatorSample.objects.values_list("name", flat=True)),
            ["Sample_1"],
        )
        target = ValidatorTarget.objects.get()
        self.assertEqual(target.name, "invA")
        self.assertEqual(target.forward_primer, "ACGTACGT")
        self.assertEqual(
            list(ValidatorPrimerSet.objects.values_list(
                "primer_name", flat=True
            )),
            ["invA"],
        )
        self.assertTrue(
            ValidatorPanel.objects.filter(
                validator_request=validator_request,
                panel="inclusivity",
                genus="Listeria",
            ).exists()
        )
        apply_async.assert_called_once_with(
            queue="cowbat",
            kwargs={"validator_request_pk": validator_request.pk},
            countdown=10,
        )
        self.assertEqual(
            response.url,
            reverse(
                "primer_finder:primer_validator_processing",
                kwargs={"validator_pk": validator_request.pk},
            ),
        )

    def test_invalid_validator_submission_does_not_save_or_queue(self):
        with patch(
            "olc_webportalv2.primer_finder.views.run_primer_validator.apply_async"
        ) as apply_async:
            response = self.client.post(
                reverse("primer_finder:primer_validator_request"),
                self.validator_post_data(
                    **{"targets-0-forward_primer": "ACGTX"}
                ),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ValidatorRequest.objects.count(), 0)
        apply_async.assert_not_called()

    def test_verifier_submission_saves_selected_panel_and_queues_analysis(self):
        data = {
            "project_name": "verifier-project",
            "primer_sequences": (
                ">gene-F\nACGTACGT\n>gene-R\nTGCATGCA\n"
            ),
            "probe_sequence": "ACGT",
            "mismatches": "2",
            "min_amplicon_size": "0",
            "max_amplicon_size": "1500",
            "range_buffer": "0",
            "inclusivity_panel": ["listeria"],
        }
        with patch(
            "olc_webportalv2.primer_finder.views.run_primer_verifier.apply_async"
        ) as apply_async:
            response = self.client.post(
                reverse("primer_finder:primer_verifier_request"),
                data,
            )

        self.assertEqual(response.status_code, 302, response.content)
        verifier_request = PrimerVerifierRequest.objects.get()
        self.assertEqual(verifier_request.user, self.user)
        self.assertEqual(verifier_request.status, "Processing")
        self.assertEqual(verifier_request.inclusivity_panel, ["listeria"])
        self.assertEqual(verifier_request.probe_sequence, "ACGT")
        self.assertTrue(
            VerifierPanel.objects.filter(
                verifier_request=verifier_request,
                panel="inclusivity",
                genus="Listeria",
            ).exists()
        )
        apply_async.assert_called_once_with(
            queue="cowbat",
            kwargs={"verifier_request_pk": verifier_request.pk},
            countdown=10,
        )
        self.assertEqual(
            response.url,
            reverse(
                "primer_finder:primer_verifier_processing",
                kwargs={"verifier_pk": verifier_request.pk},
            ),
        )

    def test_processing_pages_save_each_email_only_once(self):
        validator_request = ValidatorRequest.objects.create(
            user=self.user,
            forward_primer="ACGT",
            reverse_primer="TGCA",
        )
        verifier_request = PrimerVerifierRequest.objects.create(
            user=self.user,
            primer_sequences=">gene-F\nACGT\n>gene-R\nTGCA\n",
        )
        endpoints = (
            (
                "primer_finder:primer_validator_processing",
                {"validator_pk": validator_request.pk},
                validator_request,
            ),
            (
                "primer_finder:primer_verifier_processing",
                {"verifier_pk": verifier_request.pk},
                verifier_request,
            ),
        )

        for route_name, kwargs, request_object in endpoints:
            url = reverse(route_name, kwargs=kwargs)
            self.client.post(url, {"email": "alerts@example.org"})
            request_object.refresh_from_db()
            self.assertEqual(request_object.emails_array, ["alerts@example.org"])

            self.client.post(url, {"email": "alerts@example.org"})
            request_object.refresh_from_db()
            self.assertEqual(request_object.emails_array, ["alerts@example.org"])

    def test_foreign_requests_are_not_visible_or_deletable(self):
        other_user = User.objects.create_user(username="other-primer-user")
        validator_request = ValidatorRequest.objects.create(
            user=other_user,
            forward_primer="ACGT",
            reverse_primer="TGCA",
        )
        verifier_request = PrimerVerifierRequest.objects.create(
            user=other_user,
            primer_sequences=">gene-F\nACGT\n>gene-R\nTGCA\n",
        )
        foreign_urls = (
            reverse(
                "primer_finder:primer_validator_processing",
                kwargs={"validator_pk": validator_request.pk},
            ),
            reverse(
                "primer_finder:primer_validator_results",
                kwargs={"validator_pk": validator_request.pk},
            ),
            reverse(
                "primer_finder:primer_validator_report",
                kwargs={"validator_pk": validator_request.pk},
            ),
            reverse(
                "primer_finder:primer_verifier_processing",
                kwargs={"verifier_pk": verifier_request.pk},
            ),
            reverse(
                "primer_finder:primer_verifier_results",
                kwargs={"verifier_pk": verifier_request.pk},
            ),
        )

        for url in foreign_urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)

        self.client.post(
            reverse("primer_finder:primer_verifier_home"),
            {"delete": verifier_request.pk},
        )
        self.assertTrue(
            PrimerVerifierRequest.objects.filter(
                pk=verifier_request.pk
            ).exists()
        )
