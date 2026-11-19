"""Tests for the data-driven Primer Validator integration baseline."""

from io import BytesIO
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from openpyxl import load_workbook

from olc_webportalv2.primer_finder.integration import (
    compare_workbooks,
    load_scenario,
)
from olc_webportalv2.primer_finder.management.commands.run_primer_validator_integration import (
    Command,
)


class PrimerValidatorIntegrationFixtureTests(SimpleTestCase):
    def test_scenario_command_defaults_to_a_no_batch_dry_run(self):
        from io import StringIO

        output = StringIO()
        call_command(
            "run_primer_validator_integration",
            scenario="pk473",
            stdout=output,
        )

        self.assertIn("Dry run only", output.getvalue())
        self.assertIn("no request or Batch job was created", output.getvalue())

    @override_settings(ENVIRONMENT="PROD")
    @patch.dict(os.environ, {"PRIMER_VALIDATOR_INTEGRATION_ENABLED": "true"})
    def test_production_requires_explicit_batch_confirmation(self):
        with self.assertRaisesRegex(CommandError, "ENVIRONMENT=PROD"):
            Command._require_batch_opt_in(allow_production=False)
        Command._require_batch_opt_in(allow_production=True)

    def test_cleanup_deletes_only_the_scenario_container_and_request(self):
        request = Mock(pk=473)
        storage = Mock()
        storage.list_containers.return_value = [
            SimpleNamespace(name="primer-validator-473"),
            SimpleNamespace(name="unrelated-container"),
        ]

        Command._cleanup_run(
            request=request,
            container_name="primer-validator-473",
            storage=storage,
        )

        storage.delete_container.assert_called_once_with(
            "primer-validator-473"
        )
        request.delete.assert_called_once_with()

    def test_pk473_scenario_and_expected_artifacts_are_available(self):
        scenario_path, scenario = load_scenario("pk473")
        expected = scenario["expected"]

        self.assertEqual(scenario_path.name, "pk473.json")
        self.assertEqual(scenario["schema_version"], 1)
        self.assertTrue(
            (scenario_path.parent / expected["summary_file"]).is_file()
        )
        self.assertTrue(
            (scenario_path.parent / expected["totals_file"]).is_file()
        )
        self.assertTrue(
            (scenario_path.parent / expected["workbook_file"]).is_file()
        )

    def test_pk473_workbook_matches_its_keyed_reference_rows(self):
        scenario_path, scenario = load_scenario("pk473")
        expected = scenario["expected"]
        workbook_path = scenario_path.parent / expected["workbook_file"]

        difference_count, differences = compare_workbooks(
            expected_path=workbook_path,
            actual_content=workbook_path.read_bytes(),
            sheet_keys=expected["workbook_sheets"],
        )

        self.assertEqual((difference_count, differences), (0, []))

    def test_workbook_comparison_reports_a_changed_result_cell(self):
        scenario_path, scenario = load_scenario("pk473")
        expected = scenario["expected"]
        workbook_path = scenario_path.parent / expected["workbook_file"]
        workbook = load_workbook(workbook_path)
        worksheet = workbook["PrimerVerifierResults"]
        worksheet["H2"] = "unexpected"

        with tempfile.TemporaryDirectory() as directory:
            changed_path = Path(directory) / "changed.xlsx"
            workbook.save(changed_path)

            difference_count, differences = compare_workbooks(
                expected_path=workbook_path,
                actual_content=changed_path.read_bytes(),
                sheet_keys=expected["workbook_sheets"],
            )

        self.assertGreater(difference_count, 0)
        self.assertTrue(
            any("RESULT" in difference for difference in differences)
        )
