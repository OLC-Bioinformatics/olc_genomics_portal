"""Tests for common Batch submission helpers."""

import json
import logging
from unittest import mock

from django.test import SimpleTestCase

from olc_webportalv2.common.methods import (
    _redact_sas_urls,
    generic_api_submit,
)


class BatchSubmissionTests(SimpleTestCase):
    def test_redact_sas_urls_preserves_nested_data(self):
        value = {
            "url": "https://storage.example/blob?sig=secret",
            "items": [
                "https://storage.example/other?token=hidden",
                ("unchanged", "https://storage.example/third?sig=another"),
            ],
        }

        redacted = _redact_sas_urls(value)

        self.assertEqual(
            redacted["url"],
            "https://storage.example/blob?[REDACTED]",
        )
        self.assertEqual(
            redacted["items"][0],
            "https://storage.example/other?[REDACTED]",
        )
        self.assertIsInstance(redacted["items"][1], tuple)
        self.assertEqual(
            value["url"], "https://storage.example/blob?sig=secret"
        )

    @mock.patch("olc_webportalv2.common.methods.requests.post")
    def test_generic_api_submit_redacts_logs_and_preserves_payload(self, post):
        response = mock.Mock(
            status_code=200,
            content=b'{"job_id": "job-1"}',
            text='{"job_id": "job-1"}',
            headers={"content-type": "application/json"},
            url="https://batch.example/jobs",
            elapsed=0,
            reason="OK",
        )
        response.json.return_value = {"job_id": "job-1"}
        post.return_value = response

        with self.assertLogs(level=logging.INFO) as logs:
            result = generic_api_submit(
                command="curl https://storage.example/blob?sig=secret-value",
                container_name="job-1",
                vm_size="Standard_D4s_v3",
                download_file_pattern=["reports/*"],
                log_prefix="logs",
            )

        self.assertEqual(result, {"job_id": "job-1"})
        payload = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(payload["download_file_pattern"], ["reports/*"])
        self.assertEqual(payload["log_prefix"], "logs")
        self.assertNotIn("secret-value", "\n".join(logs.output))
        self.assertIn(
            "https://storage.example/blob?[REDACTED]",
            "\n".join(logs.output),
        )

    @mock.patch("olc_webportalv2.common.methods.requests.post")
    def test_generic_api_submit_rejects_non_json_success(self, post):
        response = mock.Mock(
            status_code=200,
            content=b"accepted",
            text="accepted",
            headers={"content-type": "text/plain"},
            url="https://batch.example/jobs",
            elapsed=0,
            reason="OK",
        )
        post.return_value = response

        with self.assertRaises(RuntimeError):
            generic_api_submit(
                command="echo run",
                container_name="job-1",
                vm_size="Standard_D4s_v3",
            )
