"""Tests for generating COWBAT pipeline progress estimates."""

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from olc_webportalv2.cowbat.percent_estimate import (
    estimate_percent_complete,
    main,
)


class PercentEstimateTests(SimpleTestCase):
    """Test COWBAT progress estimation and JSON generation."""

    def test_estimates_unique_messages_and_ignores_repeated_messages(self):
        """Ignore repeated messages and preserve endpoint percentages."""
        log_entries = "\n".join(
            (
                "2026-01-01 00:00:00 Starting pipeline",
                "2026-01-01 00:00:02 Repeated stage",
                "2026-01-01 00:00:04 Repeated stage",
                "\x1b[32m2026-01-01 00:00:10 Pipeline complete\x1b[0m",
            )
        )

        self.assertEqual(
            estimate_percent_complete(log_entries),
            [
                {"message": "Starting pipeline", "percent_complete": 0.0},
                {"message": "Pipeline complete", "percent_complete": 100.0},
            ],
        )

    def test_rejects_logs_without_positive_unique_progress(self):
        """Reject missing timestamps and timestamps with no duration."""
        with self.assertRaises(ValueError):
            estimate_percent_complete("No timestamped entries")

        log_entries = "\n".join(
            (
                "2026-01-01 00:00:00 First message",
                "2026-01-01 00:00:00 Second message",
            )
        )
        with self.assertRaises(ValueError):
            estimate_percent_complete(log_entries)

    def test_main_writes_json_to_requested_path(self):
        """Write generated progress data to the requested file."""
        log_entries = "\n".join(
            (
                "2026-01-01 00:00:00 Starting pipeline",
                "2026-01-01 00:00:10 Pipeline complete",
            )
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_path = Path(temporary_directory) / "cowbat.log"
            output_path = Path(temporary_directory) / "progress.json"
            log_path.write_text(log_entries, encoding="utf-8")

            self.assertEqual(
                main(str(log_path), str(output_path)),
                output_path,
            )
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                [
                    {"message": "Starting pipeline", "percent_complete": 0.0},
                    {
                        "message": "Pipeline complete",
                        "percent_complete": 100.0,
                    },
                ],
            )
