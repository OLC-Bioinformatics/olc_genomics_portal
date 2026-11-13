"""Regression tests for VirusTyper access controls and sequence rendering."""

from django.test import Client, TestCase
from django.urls import reverse

from olc_webportalv2.users.models import User
from olc_webportalv2.vir_typer.models import VirTyperProject
from olc_webportalv2.vir_typer.views import sequence_html_string


class VirTyperOwnershipTests(TestCase):
    """Verify users can only access and delete their own projects."""

    def setUp(self):
        self.user = User.objects.create(username="virtyper-owner")
        self.other_user = User.objects.create(username="virtyper-other")
        self.other_project = VirTyperProject.objects.create(
            user=self.other_user,
            project_name="private-project",
        )
        self.client.force_login(self.user)

    def test_project_views_hide_another_users_project(self):
        """Return 404 from upload, results, and rename for other owners."""
        for route_name in (
            "vir_typer:vir_typer_upload",
            "vir_typer:vir_typer_results",
            "vir_typer:vir_typer_rename",
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(
                    reverse(
                        route_name,
                        kwargs={"vir_typer_pk": self.other_project.pk},
                    )
                )
                self.assertEqual(response.status_code, 404)

    def test_home_does_not_delete_another_users_project(self):
        """A forged delete request cannot remove another user's project."""
        self.client.post(
            reverse("vir_typer:vir_typer_home"),
            {"delete": self.other_project.pk},
        )

        self.assertTrue(
            VirTyperProject.objects.filter(pk=self.other_project.pk).exists()
        )

    def test_home_delete_requires_a_csrf_token(self):
        """Project deletion remains protected by Django's CSRF middleware."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(
            reverse("vir_typer:vir_typer_home"),
            {"delete": self.other_project.pk},
        )

        self.assertEqual(response.status_code, 403)


class SequenceRenderingTests(TestCase):
    """Check that sequence-derived markup is safe and length-consistent."""

    def test_unknown_nucleotide_is_html_escaped(self):
        """Unrecognized sequence characters cannot inject HTML markup."""
        rendered, variable_count = sequence_html_string("<", "X")

        self.assertIn("&lt;", rendered)
        self.assertEqual(variable_count, 1)

    def test_sequence_and_consensus_lengths_must_match(self):
        """Reject a consensus that cannot be compared position by position."""
        with self.assertRaises(ValueError):
            sequence_html_string("AC", "A")
