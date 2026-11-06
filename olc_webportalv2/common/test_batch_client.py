"""Tests for the provider-neutral Batch service client."""

import unittest
from unittest.mock import Mock

from olc_webportalv2.common.batch_client import BatchServiceClient


class BatchClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.response = Mock()
        self.response.ok = True
        self.session.request.return_value = self.response
        self.client = BatchServiceClient(
            base_url="http://batch:5000",
            token="internal-token",
            session=self.session,
            timeout=10,
        )

    def test_task_listing_maps_generic_response_and_auth(self):
        self.response.json.return_value = {
            "items": [
                {"id": "job-1", "state": "completed", "exit_code": 0}
            ]
        }

        tasks = self.client.task.list("job-1")

        self.assertEqual(tasks[0].id, "job-1")
        self.assertEqual(tasks[0].state, "completed")
        self.assertEqual(tasks[0].execution_info.exit_code, 0)
        call = self.session.request.call_args
        self.assertEqual(call.args[:2], ("GET", "http://batch:5000/v1/jobs/job-1/tasks"))
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer internal-token")

    def test_resize_uses_provider_neutral_fields(self):
        self.client.pool.resize(
            pool_id="pool-1",
            target_dedicated_nodes=2,
            target_low_priority_nodes=1,
        )

        call = self.session.request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertEqual(
            call.args[1],
            "http://batch:5000/v1/pools/pool-1/resize",
        )
        self.assertEqual(
            call.kwargs["json"],
            {
                "target_dedicated_nodes": 2,
                "target_low_priority_nodes": 1,
            },
        )

    def test_job_cancel_uses_internal_route(self):
        self.client.job.cancel("job-1")

        call = self.session.request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertEqual(
            call.args[1],
            "http://batch:5000/v1/jobs/job-1/cancel",
        )

    def test_task_file_download_streams_and_closes_response(self):
        self.response.iter_content.return_value = [b"first", b"second"]

        content = self.client.file.get_from_task(
            job_id="job-1",
            task_id="task-1",
            file_path="stderr.txt",
        )

        self.assertEqual(list(content), [b"first", b"second"])
        self.response.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
