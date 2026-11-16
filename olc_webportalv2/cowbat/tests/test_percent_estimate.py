import json
import os
import tempfile
import unittest

from olc_webportalv2.cowbat.percent_estimate import (
    estimate_percent_complete,
    main,
)


class PercentEstimateTests(unittest.TestCase):
    def test_estimates_unique_messages_and_ignores_repeated_messages(self):
        log_entries = "\n".join([
            "2026-01-01 00:00:00 Starting pipeline",
            "2026-01-01 00:00:02 Repeated stage",
            "2026-01-01 00:00:04 Repeated stage",
            "2026-01-01 00:00:10 Pipeline complete",
        ])

        result = estimate_percent_complete(log_entries)

        self.assertEqual(
            result,
            [
                {"message": "Starting pipeline", "percent_complete": 0.0},
                {"message": "Pipeline complete", "percent_complete": 100.0},
            ],
        )

    def test_rejects_logs_without_progress_duration(self):
        with self.assertRaises(ValueError):
            estimate_percent_complete("No timestamped entries")

        with self.assertRaises(ValueError):
            estimate_percent_complete("\n".join([
                "2026-01-01 00:00:00 First message",
                "2026-01-01 00:00:00 Second message",
            ]))

    def test_main_writes_output_to_requested_path(self):
        log_entries = "\n".join([
            "2026-01-01 00:00:00 Starting pipeline",
            "2026-01-01 00:00:10 Pipeline complete",
        ])
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_path = os.path.join(temporary_directory, "cowbat.log")
            output_path = os.path.join(
                temporary_directory, "cowbat_percent_complete.json"
            )
            with open(log_path, "w") as log_file:
                log_file.write(log_entries)

            self.assertEqual(main(log_path, output_path), output_path)
            with open(output_path, "r") as output_file:
                self.assertEqual(
                    json.load(output_file),
                    [
                        {"message": "Starting pipeline", "percent_complete": 0.0},
                        {"message": "Pipeline complete", "percent_complete": 100.0},
                    ],
                )
