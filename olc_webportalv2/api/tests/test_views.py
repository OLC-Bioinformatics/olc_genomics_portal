import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from rest_framework.test import APITestCase

from olc_webportalv2.cowbat.models import SequencingRun
from olc_webportalv2.users.models import User


class InMemoryStorage:
    def __init__(self):
        self.objects = {}

    def ensure_container(self, _container):
        pass

    def upload_bytes(self, container, name, content, content_type=None):
        self.objects[(container, name)] = content

    def object_exists(self, container, name):
        return (container, name) in self.objects

    def get_object(self, container, name):
        return SimpleNamespace(size=len(self.objects[(container, name)]))

    def list_objects(self, container):
        return [
            SimpleNamespace(name=name)
            for stored_container, name in self.objects
            if stored_container == container
        ]


class TestAPI(APITestCase):
    def setUp(self):
        super().setUp()
        self.storage = InMemoryStorage()
        storage_patcher = patch(
            "olc_webportalv2.api.views.get_storage_client",
            return_value=self.storage,
        )
        storage_patcher.start()
        self.addCleanup(storage_patcher.stop)

        user = User.objects.create(username="TestUser")
        user.set_password("password")
        user.save()

    def test_upload_get_requires_auth(self):
        response = self.client.get("/en-ca/api/upload/fake_run/fake_file")
        self.assertEqual(
            response.status_code, 403
        )  # Should be forbidden if not logged in

    def test_upload_put_requires_auth(self):
        response = self.client.put("/en-ca/api/upload/fake_run/fake_file")
        self.assertEqual(
            response.status_code, 403
        )  # Should be forbidden if not logged in

    def test_file_does_not_exist(self):
        self.client.login(username="TestUser", password="password")
        response = self.client.get(
            "/en-ca/api/upload/fake_run/fake_file", format="json"
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertFalse(response_dict["exists"])
        self.assertEqual(response_dict["size"], 0)

    def test_file_upload_samplesheet(self):
        self.client.login(username="TestUser", password="password")
        with open(
            "olc_webportalv2/test_files/SampleSheet.csv", "rb"
        ) as infile:
            response = self.client.put(
                "/en-ca/api/upload/111111_FAKE/SampleSheet.csv",
                infile.read(),
                content_type="text/csv",
            )
        sequencing_run = SequencingRun.objects.get(run_name="111111_FAKE")
        blob_exists = self.storage.object_exists(
            "111111-fake", "SampleSheet.csv"
        )
        self.assertTrue(blob_exists, self.storage.objects)
        self.assertIn("2018-SEQ-1471", sequencing_run.seqids)
        self.assertEqual(len(sequencing_run.seqids), 24)
        self.assertEqual(response.status_code, 204)

    def test_file_upload_fastq(self):
        self.client.login(username="TestUser", password="password")
        with open(
            "olc_webportalv2/test_files/2018-SEQ-1471_S1_L001_R1_001.fastq.gz",
            "rb",
        ) as infile:
            response = self.client.put(
                "/en-ca/api/upload/111111_FAKE/"
                "2018-SEQ-1471_S1_L001_R1_001.fastq.gz",
                infile.read(),
                content_type="application/gzip",
            )
        blob_exists = self.storage.object_exists(
            "111111-fake", "2018-SEQ-1471_S1_L001_R1_001.fastq.gz"
        )
        self.assertTrue(blob_exists)
        self.assertEqual(response.status_code, 204)

    def test_file_upload_interop(self):
        self.client.login(username="TestUser", password="password")
        with open(
            "olc_webportalv2/test_files/InterOp/QMetricsOut.bin", "rb"
        ) as infile:
            response = self.client.put(
                "/en-ca/api/upload/111111_FAKE/QMetricsOut.bin",
                infile.read(),
                content_type="application/octet-stream",
            )
        self.assertEqual(response.status_code, 204, response.content)
        blob_exists = self.storage.object_exists(
            "111111-fake", "InterOp/QMetricsOut.bin"
        )
        self.assertTrue(blob_exists, self.storage.objects)
        self.assertEqual(response.status_code, 204)

    def test_file_upload_metadata_file(self):
        self.client.login(username="TestUser", password="password")
        with open("olc_webportalv2/test_files/RunInfo.xml", "rb") as infile:
            response = self.client.put(
                "/en-ca/api/upload/111111_FAKE/RunInfo.xml",
                infile.read(),
                content_type="text/xml",
            )
        blob_exists = self.storage.object_exists("111111-fake", "RunInfo.xml")
        self.assertTrue(blob_exists)
        self.assertEqual(response.status_code, 204)

    def test_get_interop_file_exists(self):
        self.storage.upload_bytes(
            "111111-fake",
            "InterOp/QMetricsOut.bin",
            b"fake interop data",
        )
        self.client.login(username="TestUser", password="password")
        response = self.client.get(
            "/en-ca/api/upload/111111_FAKE/QMetricsOut.bin"
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertTrue(response_dict["exists"])
        self.assertGreater(response_dict["size"], 0)

    def test_get_interop_file_does_not_exist(self):
        self.client.login(username="TestUser", password="password")
        response = self.client.get(
            "/en-ca/api/upload/111111_FAKE/ControlMetricsOut.bin"
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertFalse(response_dict["exists"])
        self.assertEqual(response_dict["size"], 0)

    def test_get_metadata_file_exists(self):
        self.storage.upload_bytes(
            "111111-fake",
            "RunInfo.xml",
            b"<RunInfo/>",
        )
        self.client.login(username="TestUser", password="password")
        response = self.client.get("/en-ca/api/upload/111111_FAKE/RunInfo.xml")
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertTrue(response_dict["exists"])
        self.assertGreater(response_dict["size"], 0)

    def test_get_metadata_file_does_not_exist(self):
        self.client.login(username="TestUser", password="password")
        response = self.client.get(
            "/en-ca/api/upload/111111_FAKE/runParameters.xml"
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertFalse(response_dict["exists"])
        self.assertEqual(response_dict["size"], 0)

    def test_get_fastq_file_exists(self):
        self.storage.upload_bytes(
            "111111-fake",
            "2018-SEQ-1471_S1_L001_R1_001.fastq.gz",
            b"fake fastq data",
        )
        self.client.login(username="TestUser", password="password")
        response = self.client.get(
            "/en-ca/api/upload/111111_FAKE/"
            "2018-SEQ-1471_S1_L001_R1_001.fastq.gz"
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertTrue(response_dict["exists"])
        self.assertGreater(response_dict["size"], 0)

    def test_get_fastq_file_does_not_exist(self):
        self.client.login(username="TestUser", password="password")
        response = self.client.get(
            "/en-ca/api/upload/111111_FAKE/"
            "2018-SEQ-1471_not_real_L001_R1_001.fastq.gz"
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertFalse(response_dict["exists"])
        self.assertEqual(response_dict["size"], 0)

    def test_email_relay_requires_secret_header(self):
        payload = {
            "subject": "Test",
            "body": "Hello",
            "recipient": "test@example.com",
        }
        response = self.client.post(
            "/en-ca/api/email_relay/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    @patch.dict(os.environ, {"EMAIL_RELAY_SECRET": "secret"})
    def test_email_relay_missing_fields(self):
        payload = {"subject": "Test", "body": "Hello"}
        response = self.client.post(
            "/en-ca/api/email_relay/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_EMAIL_RELAY_SECRET="secret",
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response_dict["status"], "failure")
        self.assertIn("recipient", response_dict["fields"])

    @patch("olc_webportalv2.api.views.send_email")
    @patch.dict(os.environ, {"EMAIL_RELAY_SECRET": "secret"})
    def test_email_relay_success(self, mock_send_email):
        payload = {
            "subject": "Test",
            "body": "Hello",
            "recipient": "test@example.com",
        }
        response = self.client.post(
            "/en-ca/api/email_relay/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_EMAIL_RELAY_SECRET="secret",
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response_dict["status"], "success")
        mock_send_email.assert_called_once_with(
            subject="Test",
            body="Hello",
            recipient="test@example.com",
            sender_name=None,
        )

    @patch("olc_webportalv2.api.views.send_email")
    @patch.dict(os.environ, {"EMAIL_RELAY_SECRET": "secret"})
    def test_email_relay_accepts_sender_name(self, mock_send_email):
        payload = {
            "subject": "Test sender name",
            "body": "Hello with display name",
            "recipient": "test@example.com",
            "sender_name": "CFIA Local Redmine",
        }
        response = self.client.post(
            "/en-ca/api/email_relay/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_EMAIL_RELAY_SECRET="secret",
        )
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response_dict["status"], "success")
        mock_send_email.assert_called_once_with(
            subject="Test sender name",
            body="Hello with display name",
            recipient="test@example.com",
            sender_name="CFIA Local Redmine",
        )

    def test_start_cowbat_login_required(self):
        response = self.client.get("/en-ca/api/run_cowbat/111111_FAKE")
        self.assertEqual(response.status_code, 403)

    def test_start_cowbat_files_not_present(self):
        sequencing_run = SequencingRun.objects.create(
            run_name="111111_FAKE", seqids=["2018-SEQ-1471", "2018-SEQ-1472"]
        )
        sequencing_run.save()
        self.client.login(username="TestUser", password="password")
        response = self.client.get("/en-ca/api/run_cowbat/111111_FAKE")
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertEqual(
            response_dict["status"],
            "Some files were missing. Could not start assembly.",
        )

    def test_start_cowbat_already_done(self):
        sequencing_run = SequencingRun.objects.create(
            run_name="111111_FAKE", status="Complete"
        )
        sequencing_run.save()
        self.client.login(username="TestUser", password="password")
        response = self.client.get("/en-ca/api/run_cowbat/111111_FAKE")
        response_dict = json.loads(response.content.decode("utf-8"))
        self.assertIn("Did not start", response_dict["status"])
