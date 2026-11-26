import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.ampliseq.models import AmpliSeqRequest
from olc_webportalv2.ampliseq.tasks import (
    create_sas_urls,
    find_containers,
    upload_file,
)
from olc_webportalv2.users.models import User


class AmpliSeqHistoryProjectionTests(TestCase):
    def test_home_defers_uploaded_files(self):
        user = User.objects.create_user(username="ampliseq-home-test")
        self.client.force_login(user)
        project = AmpliSeqRequest.objects.create(user=user)

        response = self.client.get(reverse("ampliseq:ampliseq"))
        projects = list(response.context["projects"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(projects[0].pk, project.pk)
        self.assertTrue(
            {"metadata", "classifier"}.issubset(
                projects[0].get_deferred_fields()
            )
        )


class AmpliSeqStorageTests(unittest.TestCase):
    @patch("olc_webportalv2.ampliseq.tasks.get_storage_client")
    def test_find_containers_uses_generic_container_names(self, get_client):
        client = get_client.return_value
        client.list_containers.return_value = [
            SimpleNamespace(name="ampliseq-request"),
            SimpleNamespace(name="123456-run"),
            SimpleNamespace(name="unrelated"),
            SimpleNamespace(name="prefix-ampliseq"),
        ]

        self.assertEqual(
            find_containers(),
            ["123456-run", "ampliseq-request"],
        )
        client.list_containers.assert_called_once_with()

    @patch("olc_webportalv2.ampliseq.tasks.get_storage_client")
    def test_upload_file_ensures_container_and_uploads_bytes(self, get_client):
        client = get_client.return_value
        uploaded_file = SimpleNamespace(
            name="metadata.csv",
            content_type="text/csv",
            read=Mock(return_value=b"metadata"),
        )

        upload_file(uploaded_file, "ampliseq-request")

        client.ensure_container.assert_called_once_with("ampliseq-request")
        client.upload_bytes.assert_called_once_with(
            "ampliseq-request",
            "metadata.csv",
            b"metadata",
            content_type="text/csv",
        )

    @patch("olc_webportalv2.ampliseq.tasks.get_storage_client")
    def test_create_sas_urls_requests_each_report(self, get_client):
        client = get_client.return_value
        client.create_transfer_url.side_effect = [
            "report-url",
            "trace-url",
            "timeline-url",
        ]
        request = SimpleNamespace(container_name="ampliseq-1")

        self.assertEqual(
            create_sas_urls(request),
            ("report-url", "trace-url", "timeline-url"),
        )
        self.assertEqual(
            client.create_transfer_url.call_args_list,
            [
                call(
                    "ampliseq-1",
                    "download",
                    name=(
                        "ampliseq-1/reports/ampliseq-1_execution_report.html"
                    ),
                    expires_in_seconds=8 * 24 * 60 * 60,
                ),
                call(
                    "ampliseq-1",
                    "download",
                    name=("ampliseq-1/reports/ampliseq-1_execution_trace.txt"),
                    expires_in_seconds=8 * 24 * 60 * 60,
                ),
                call(
                    "ampliseq-1",
                    "download",
                    name=(
                        "ampliseq-1/reports/ampliseq-1_execution_timeline.html"
                    ),
                    expires_in_seconds=8 * 24 * 60 * 60,
                ),
            ],
        )
