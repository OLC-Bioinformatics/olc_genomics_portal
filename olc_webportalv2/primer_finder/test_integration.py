"""Tests for the data-driven Primer Validator integration baseline."""

from io import BytesIO
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from openpyxl import Workbook, load_workbook

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

    def test_gene_scenario_contains_reference_and_baseline_artifacts(self):
        scenario_path, scenario = load_scenario("pk473_gene")
        target = scenario["input"]["samples"][0]["targets"][0]
        self.assertEqual(len(target["gene_sequence"]), 1590)
        self.assertEqual(scenario["expected"]["gene_target"], target["name"])
        self.assertEqual(scenario["input"]["inclusivity_panel"], ["bds-listeria"])
        self.assertEqual(scenario["input"]["exclusivity_panel"], [])
        Command._validate_fixture_files(scenario_path, scenario["expected"])

    def test_reference_projection_requires_all_bds_rows_only(self):
        baseline = Workbook()
        baseline.active.title = "PrimerVerifierResults"
        baseline.active.append(["PANEL", "BENCHMARK PANEL", "SEQID", "result"])
        baseline.active.append(["INCLUSIVITY", "BDS-Listeria", "bds", "pass"])
        baseline.active.append(["INCLUSIVITY", "Listeria", "other", "pass"])
        baseline.active.append(["EXCLUSIVITY", "Other", "excluded", "pass"])
        pseudo = baseline.create_sheet("Pseudoalignments")
        pseudo.append(["PANEL", "SEQID", "Primer"])
        pseudo.append(["INCLUSIVITY", "bds", "HLY"])
        pseudo.append(["INCLUSIVITY", "other", "HLY"])
        actual = Workbook()
        actual.active.title = "PrimerVerifierResults"
        actual.active.append(["PANEL", "BENCHMARK PANEL", "SEQID", "result"])
        actual.active.append(["INCLUSIVITY", "BDS-Listeria", "bds", "pass"])
        pseudo = actual.create_sheet("Pseudoalignments")
        pseudo.append(["PANEL", "SEQID", "Primer"])
        pseudo.append(["INCLUSIVITY", "bds", "HLY"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.xlsx"
            baseline.save(path)
            output = BytesIO()
            actual.save(output)
            options = {
                "expected_path": path,
                "actual_content": output.getvalue(),
                "sheet_keys": {
                    "PrimerVerifierResults": ["PANEL", "BENCHMARK PANEL", "SEQID"],
                    "Pseudoalignments": ["PANEL", "SEQID", "Primer"],
                },
                "reference_panel": "BDS-Listeria",
            }
            self.assertEqual(compare_workbooks(**options), (0, []))
            actual.active.delete_rows(2)
            output = BytesIO()
            actual.save(output)
            options["actual_content"] = output.getvalue()
            self.assertGreater(compare_workbooks(**options)[0], 0)

    def test_workbook_projection_preserves_baseline_comparison(self):
        expected = Workbook()
        expected.active.title = "PrimerVerifierResults"
        expected.active.append(["SEQID", "result"])
        expected.active.append(["isolate", "pass"])
        expected.create_sheet("GeneCategoryAudit").append(["report"])
        actual = Workbook()
        actual.active.title = "PrimerVerifierResults"
        actual.active.append(["SEQID", "result", "gene match"])
        actual.active.append(["isolate", "pass", 99.0])
        audit = actual.create_sheet("GeneCategoryAudit")
        audit.append(["report"])
        audit.append(["isolate"])
        output = BytesIO()
        actual.save(output)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.xlsx"
            expected.save(path)
            options = {
                "expected_path": path,
                "actual_content": output.getvalue(),
                "sheet_keys": {"PrimerVerifierResults": ["SEQID"]},
                "actual_extra_columns": {"PrimerVerifierResults": ["gene match"]},
                "header_only_sheets": ["GeneCategoryAudit"],
            }
            self.assertEqual(compare_workbooks(**options), (0, []))
            actual.active["B2"] = "fail"
            output = BytesIO()
            actual.save(output)
            options["actual_content"] = output.getvalue()
            self.assertGreater(compare_workbooks(**options)[0], 0)

    def test_gene_checks_require_completed_jobs_and_actual_results(self):
        workbook = Workbook()
        workbook.active.title = "PrimerVerifierResults"
        workbook.active.append(["SEQID", "Gene Sequence Percent Match  HLY"])
        workbook.active.append(["isolate", 99.0])
        workbook.create_sheet("GeneCategoryAudit").append(["report"])
        output = BytesIO()
        workbook.save(output)
        hits = [{
            "target": "HLY__HLY", "identity": "99",
            "report": "isolate_blastn_geneseekr.tsv",
        }]
        reports = {"isolate_blastn_geneseekr.tsv": "gene result"}
        request = SimpleNamespace(
            analysis_tasks=SimpleNamespace(
                values_list=Mock(return_value=[("primer", "complete"), ("gene", "complete")])
            ),
            gene_report=json.dumps(reports),
            gene_results=hits,
            gene_exclusion_results={},
            gene_exclusion_details={},
        )
        evaluation = {
            "normalized_hits": hits, "categories": {},
            "category_details": {}, "gene_reports": reports,
        }
        storage = Mock()
        storage.download_bytes.side_effect = [
            json.dumps(evaluation).encode(), b"report\ttarget\tcategory\n"
        ]
        options = {
            "request": request, "storage": storage, "container_name": "test",
            "workbook_content": output.getvalue(), "target": "HLY",
        }
        Command._check_gene_results(**options)
        workbook.active["B2"] = 42
        output = BytesIO()
        workbook.save(output)
        options["workbook_content"] = output.getvalue()
        storage.download_bytes.side_effect = [
            json.dumps(evaluation).encode(), b"report\ttarget\tcategory\n"
        ]
        with self.assertRaisesRegex(AssertionError, "consistent with a stored hit"):
            Command._check_gene_results(**options)
        request.analysis_tasks.values_list.return_value = [("primer", "complete")]
        with self.assertRaisesRegex(AssertionError, "both complete"):
            Command._check_gene_results(**options)
        request.analysis_tasks.values_list.return_value = [
            ("primer", "complete"), ("gene", "complete")
        ]
        request.gene_results = []
        with self.assertRaisesRegex(AssertionError, "no normalized hit"):
            Command._check_gene_results(**options)

    def test_cleanup_waits_for_every_job_and_resource(self):
        tasks = Mock()
        request = SimpleNamespace(status="Error", analysis_tasks=tasks)
        tasks.values_list.return_value = [
            ("primer", "error", ""), ("gene", "submitted", "gene-job")
        ]
        required = {"primer", "gene"}
        self.assertFalse(Command._batch_resources_cleaned(request, required_task_types=required))
        tasks.values_list.return_value = [
            ("primer", "error", ""), ("gene", "complete", "gene-job")
        ]
        self.assertFalse(Command._batch_resources_cleaned(request, required_task_types=required))
        tasks.values_list.return_value = [("primer", "error", ""), ("gene", "complete", "")]
        self.assertTrue(Command._batch_resources_cleaned(request, required_task_types=required))
        tasks.values_list.return_value = [("primer", "complete", "")]
        self.assertFalse(Command._batch_resources_cleaned(request, required_task_types=required))
        tasks.values_list.return_value = []
        self.assertFalse(Command._batch_resources_cleaned(request, required_task_types=required))
