from unittest import mock

from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.cowbat.models import SequencingRun
from olc_webportalv2.users.models import User


class ResearchAssemblyUiActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="research-assembly-ui",
            password="password",
        )
        self.client.login(
            username="research-assembly-ui",
            password="password",
        )

    def test_research_assembly_cards_link_to_their_workflows(self):
        response = self.client.get(reverse("cowbat:research_assembly"))

        self.assertEqual(response.status_code, 200)
        for route_name in (
            "cowbat:blob_assembly",
            "cowbat:upload_assembly",
            "cowbat:manage_assembly",
        ):
            with self.subTest(route=route_name):
                self.assertContains(
                    response,
                    'href="{0}"'.format(reverse(route_name)),
                )

    def test_open_run_form_targets_the_run_manager(self):
        response = self.client.get(reverse("cowbat:manage_assembly"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<form method="get" action="{0}"'.format(
                reverse("cowbat:manage_assembly")
            ),
        )
        self.assertContains(response, 'type="submit">Open run</button>')

    @mock.patch("olc_webportalv2.cowbat.views.get_research_storage")
    def test_run_controls_are_wired_to_their_actions(self, storage_factory):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name="240824-ui-run",
            container="240824-ui-run",
        )
        storage_factory.return_value.list_file_entries.return_value = []

        response = self.client.get(
            reverse(
                "cowbat:manage_assembly_run",
                kwargs={"sequencing_run_pk": run.pk},
            )
        )
        delete_url = reverse(
            "cowbat:delete_research_files",
            kwargs={"sequencing_run_pk": run.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<form id="rename-form" method="post"')
        self.assertContains(
            response,
            '<input type="hidden" name="action" value="rename">',
        )
        self.assertContains(response, 'id="rename-button"')
        self.assertContains(response, 'action="{0}"'.format(delete_url))
        self.assertContains(response, 'id="select-all"')
        self.assertContains(response, 'name="delete_all" value="1"')
        self.assertContains(response, 'id="upload-dropzone"')
        self.assertContains(response, 'role="button" tabindex="0"')
        self.assertContains(response, 'id="upload-files" class="btn btn-primary" type="button" disabled')
        self.assertContains(
            response,
            '<input type="hidden" name="action" value="submit">',
        )
        self.assertContains(response, 'type="submit">Submit run</button>')
