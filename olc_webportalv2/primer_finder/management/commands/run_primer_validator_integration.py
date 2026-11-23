"""Run an opt-in Primer Validator workflow against a batch service."""

import csv
import hashlib
import json
import os
import time
import uuid
from io import BytesIO, StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from olc_webportalv2.common.benchmarks import (
    benchmark_panel_storage_name,
)
from olc_webportalv2.common.storage_client import (
    StorageServiceError,
    get_storage_client,
)
from olc_webportalv2.primer_finder.integration import (
    INTEGRATION_DATA_DIR,
    compare_workbooks,
    load_scenario,
)
from olc_webportalv2.primer_finder.methods import populate_panel
from olc_webportalv2.primer_finder.models import (
    ValidatorPanel,
    ValidatorPrimerSet,
    ValidatorPrimers,
    ValidatorRequest,
    ValidatorSample,
    ValidatorTarget,
)
from olc_webportalv2.primer_finder.tasks import run_primer_validator
from olc_webportalv2.primer_finder.validator_gene_reports import _gene_report_key


class Command(BaseCommand):
    help = (
        "Compare a Primer Validator Batch run with a checked-in reference. "
        "Pass --run-batch and set PRIMER_VALIDATOR_INTEGRATION_ENABLED=true "
        "to submit a real job."
    )

    def add_arguments(self, parser):
        parser.add_argument("--scenario", default="pk473")
        parser.add_argument(
            "--run-batch",
            action="store_true",
            help="Submit the scenario to the configured Batch service.",
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Explicitly allow a run when ENVIRONMENT=PROD.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=7200,
            help="Maximum wait for completion, in seconds.",
        )
        parser.add_argument(
            "--poll-interval",
            type=int,
            default=15,
            help="Seconds between status checks.",
        )
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Keep the successful request and its output container.",
        )

    def handle(self, *args, **options):
        try:
            scenario_path, scenario = load_scenario(options["scenario"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError(str(exc)) from exc

        self._validate_timing_options(options)
        input_data = scenario["input"]
        expected = scenario["expected"]
        required_task_types = {"primer"}
        if expected.get("gene_target"):
            required_task_types.add("gene")
        self._validate_fixture_files(scenario_path, expected)

        if not options["run_batch"]:
            gene_count = sum(
                bool(target.get("gene_sequence"))
                for sample in input_data["samples"]
                for target in sample["targets"]
            )
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only; no request or Batch job was created."
                )
            )
            self.stdout.write(
                f"Scenario: {options['scenario']}; "
                f"samples: {len(input_data['samples'])}; "
                f"targets: {sum(len(s['targets']) for s in input_data['samples'])}; "
                f"gene references: {gene_count}; "
                f"expected benchmark versions: "
                f"{expected['benchmark_versions']}"
            )
            self.stdout.write(
                "To opt in to a real Batch submission, set "
                "PRIMER_VALIDATOR_INTEGRATION_ENABLED=true and pass "
                "--run-batch. ENVIRONMENT=PROD also requires "
                "--allow-production."
            )
            return

        self._require_batch_opt_in(
            allow_production=options["allow_production"]
        )
        storage = get_storage_client()
        self._verify_benchmark_sources(
            storage=storage,
            benchmark_sources=expected["benchmark_sources"],
        )

        request = self._create_request(
            scenario_name=options["scenario"],
            input_data=input_data,
        )
        container_name = request.container_namer()
        self.stdout.write(
            f"Created integration request {request.pk} "
            f"({container_name}). Submitting to the Batch queue."
        )

        terminal = False
        try:
            run_primer_validator.apply_async(
                queue="cowbat",
                kwargs={"validator_request_pk": request.pk},
                countdown=10,
            )
            self._wait_for_completion(
                request=request,
                timeout=options["timeout"],
                poll_interval=options["poll_interval"],
                required_task_types=required_task_types,
            )
            terminal = True
            self._compare_results(
                request=request,
                container_name=container_name,
                scenario_path=scenario_path,
                expected=expected,
                storage=storage,
            )
        except (
            AssertionError,
            RuntimeError,
            StorageServiceError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            request.refresh_from_db()
            terminal = terminal or self._batch_resources_cleaned(
                request, required_task_types=required_task_types
            )
            cleanup_status = (
                "The request and outputs were retained because the Batch "
                "run may still be active."
            )
            if terminal and not options["keep"]:
                try:
                    self._cleanup_run(
                        request=request,
                        container_name=container_name,
                        storage=storage,
                    )
                    cleanup_status = (
                        "The terminal test request and output container "
                        "were removed."
                    )
                except CommandError as cleanup_error:
                    cleanup_status = str(cleanup_error)
            raise CommandError(
                f"Primer Validator integration scenario "
                f"{options['scenario']} failed for request {request.pk}: "
                f"{exc}. {cleanup_status}"
            ) from exc

        if options["keep"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Scenario {options['scenario']} passed. Kept request {request.pk} "
                    f"and container {container_name}."
                )
            )
            return

        self._cleanup_run(
            request=request,
            container_name=container_name,
            storage=storage,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Scenario {options['scenario']} passed; the test request "
                "and output container were removed."
            )
        )

    @staticmethod
    def _cleanup_run(*, request, container_name, storage):
        try:
            container_names = {
                container.name for container in storage.list_containers()
            }
            if container_name in container_names:
                storage.delete_container(container_name)
        except StorageServiceError as exc:
            raise CommandError(
                f"Unable to remove test container {container_name}: {exc}. "
                f"Request {request.pk} was kept."
            ) from exc
        request.delete()

    @staticmethod
    def _validate_timing_options(options):
        if options["timeout"] <= 0:
            raise CommandError("--timeout must be greater than zero.")
        if options["poll_interval"] <= 0:
            raise CommandError("--poll-interval must be greater than zero.")

    @staticmethod
    def _validate_fixture_files(scenario_path, expected):
        for key in ("summary_file", "totals_file", "workbook_file"):
            artifact = scenario_path.parent / expected[key]
            if not artifact.is_file():
                raise CommandError(
                    f"Expected reference artifact is missing: {artifact}"
                )

    @staticmethod
    def _require_batch_opt_in(*, allow_production):
        if os.environ.get(
            "PRIMER_VALIDATOR_INTEGRATION_ENABLED", ""
        ).lower() not in {"1", "true", "yes"}:
            raise CommandError(
                "Batch submission is disabled. Set "
                "PRIMER_VALIDATOR_INTEGRATION_ENABLED=true explicitly."
            )
        if (
            str(getattr(settings, "ENVIRONMENT", "")).upper() == "PROD"
            and not allow_production
        ):
            raise CommandError(
                "Refusing to submit an integration job while "
                "ENVIRONMENT=PROD without --allow-production."
            )

    @staticmethod
    def _verify_benchmark_sources(*, storage, benchmark_sources):
        for blob_name, expected_etag in benchmark_sources.items():
            try:
                actual_etag = storage.get_object(
                    "benchmark-datasets", blob_name
                ).etag
            except StorageServiceError as exc:
                raise CommandError(
                    f"Cannot verify benchmark input {blob_name}: {exc}"
                ) from exc
            if actual_etag != expected_etag:
                raise CommandError(
                    f"Benchmark input {blob_name} changed since the "
                    f"baseline was captured: expected ETag {expected_etag}, "
                    f"got {actual_etag}. Review and deliberately refresh "
                    "the scenario before running it."
                )

    @staticmethod
    def _create_request(*, scenario_name, input_data):
        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="primer-validator-integration",
            defaults={"is_active": False, "is_staff": False},
        )
        project_name = (
            f"integration-{scenario_name}-{uuid.uuid4().hex[:12]}"
        )

        with transaction.atomic():
            request = ValidatorRequest.objects.create(
                user=user,
                project_name=project_name,
                status="Processing",
                mismatches=input_data["mismatches"],
                min_amplicon_size=input_data["min_amplicon_size"],
                max_amplicon_size=input_data["max_amplicon_size"],
                contig_breaks=input_data["contig_breaks"],
                range_buffer=input_data["range_buffer"],
                primer_sequences=input_data["primer_sequences"],
                forward_primer=input_data["forward_primer"],
                reverse_primer=input_data["reverse_primer"],
                inclusivity_panel=[
                    benchmark_panel_storage_name(benchmark_name=name)
                    for name in input_data["inclusivity_panel"]
                ],
                exclusivity_panel=[
                    benchmark_panel_storage_name(benchmark_name=name)
                    for name in input_data["exclusivity_panel"]
                ],
                gene_exclusion_categories=input_data[
                    "gene_exclusion_categories"
                ],
            )
            for sample_data in input_data["samples"]:
                sample = ValidatorSample.objects.create(
                    validator_request=request,
                    name=sample_data["name"],
                    logic=sample_data["logic"],
                    classification_rule=sample_data[
                        "classification_rule"
                    ],
                )
                for target_data in sample_data["targets"]:
                    target = ValidatorTarget.objects.create(
                        sample=sample,
                        **target_data,
                    )
                    primer_set = ValidatorPrimerSet.objects.create(
                        validator_request=request,
                        primer_name=target.name,
                    )
                    ValidatorPrimers.objects.create(
                        primer=primer_set,
                        primer_header=f"{target.name}-F",
                        primer_sequence=target.forward_primer,
                    )
                    ValidatorPrimers.objects.create(
                        primer=primer_set,
                        primer_header=f"{target.name}-R",
                        primer_sequence=target.reverse_primer,
                    )

            populate_panel(
                genera=request.inclusivity_panel,
                pk=request.pk,
                panel_type="inclusivity",
                panel=ValidatorPanel,
            )
            populate_panel(
                genera=request.exclusivity_panel,
                pk=request.pk,
                panel_type="exclusivity",
                panel=ValidatorPanel,
            )
        return request

    @staticmethod
    def _wait_for_completion(
        *, request, timeout, poll_interval, required_task_types
    ):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            request.refresh_from_db()
            if request.status == "Complete" and Command._batch_resources_cleaned(
                request, required_task_types=required_task_types
            ):
                return
            if request.status == "Error":
                raise RuntimeError(request.errors or "request status is Error")
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))

        request.refresh_from_db()
        raise TimeoutError(
            f"request remained {request.status!r} or Batch resources were "
            f"not cleaned up after {timeout} seconds"
        )

    @staticmethod
    def _batch_resources_cleaned(request, *, required_task_types):
        tasks = list(
            request.analysis_tasks.values_list(
                "task_type", "status", "batch_job_id"
            )
        )
        return (
            request.status in {"Complete", "Error"}
            and {task_type for task_type, _, _ in tasks} == required_task_types
            and all(
                status in {"complete", "error"} and not job_id
                for _, status, job_id in tasks
            )
        )

    def _compare_results(
        self,
        *,
        request,
        container_name,
        scenario_path,
        expected,
        storage,
    ):
        objects = {
            item.name for item in storage.list_objects(container_name)
        }
        expected_report_objects = {
            "reports/"
            + filename.format(container=container_name)
            for filename in expected["required_report_files"]
        }
        expected_report_objects.add(
            expected["archive_file"].format(container=container_name)
        )
        missing_objects = expected_report_objects - objects
        if missing_objects:
            raise AssertionError(
                f"Missing report files: {sorted(missing_objects)!r}"
            )

        if request.benchmark_versions != expected["benchmark_versions"]:
            raise AssertionError(
                "Benchmark versions differ: expected "
                f"{expected['benchmark_versions']!r}, got "
                f"{request.benchmark_versions!r}"
            )

        report = json.loads(request.validator_original_report)
        if report.get("validator_version") != expected["validator_version"]:
            raise AssertionError(
                "Primer Validator version differs: expected "
                f"{expected['validator_version']!r}, got "
                f"{report.get('validator_version')!r}"
            )
        canonical_report = json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        report_hash = hashlib.sha256(canonical_report).hexdigest()
        expected_hash = expected.get("canonical_report_sha256")
        if expected_hash is not None and report_hash != expected_hash:
            raise AssertionError(
                "Raw Primer Validator report differs from the pk473 "
                f"reference: expected SHA-256 "
                f"{expected_hash}, got {report_hash}"
            )

        for model_field in ("summary", "totals"):
            expected_path = (
                scenario_path.parent / expected[f"{model_field}_file"]
            )
            expected_value = json.loads(
                expected_path.read_text(encoding="utf-8")
            )
            actual_value = json.loads(getattr(request, model_field))
            if expected.get("reference_panel_key"):
                target = expected["gene_target"]
                panel = expected["reference_panel_key"]
                try:
                    expected_value = expected_value[target]["inclusivity"][panel]
                    actual_value = actual_value[target]["inclusivity"][panel]
                except KeyError as exc:
                    raise AssertionError(
                        f"Missing {panel} {model_field} reference or result."
                    ) from exc
            if actual_value != expected_value:
                raise AssertionError(
                    f"Stored model {model_field} differs from the pk473 "
                    "reference."
                )

        workbook_name = (
            f"reports/{container_name}_summary_report.xlsx"
        )
        actual_workbook = storage.download_bytes(
            container_name, workbook_name
        )
        workbook_path = (
            scenario_path.parent / expected["workbook_file"]
        )
        gene_target = expected.get("gene_target")
        if gene_target:
            self._check_gene_results(
                request=request, storage=storage,
                container_name=container_name,
                workbook_content=actual_workbook, target=gene_target,
            )
        else:
            if len(request.gene_results) != expected["gene_results_count"]:
                raise AssertionError(
                    "Gene result count differs: expected "
                    f"{expected['gene_results_count']}, got "
                    f"{len(request.gene_results)}"
                )
            if request.gene_exclusion_results != expected[
                "gene_exclusion_results"
            ]:
                raise AssertionError(
                    "Gene exclusion results differ from the pk473 reference."
                )
        difference_count, differences = compare_workbooks(
            expected_path=workbook_path,
            actual_content=actual_workbook,
            sheet_keys=expected["workbook_sheets"],
            actual_extra_columns=(
                {"PrimerVerifierResults": [
                    f"Gene Sequence Percent Match  {gene_target}"
                ]} if gene_target else None
            ),
            header_only_sheets=["GeneCategoryAudit"] if gene_target else None,
            reference_panel=expected.get("reference_panel"),
        )
        if difference_count:
            details = "; ".join(differences)
            raise AssertionError(
                f"Summary workbook differs in {difference_count} places: "
                f"{details}"
            )

        summary_blob = storage.download_bytes(
            container_name,
            f"reports/{container_name}_summary_report.json",
        )
        if json.loads(summary_blob) != json.loads(request.summary):
            raise AssertionError(
                "Summary JSON output differs from the stored model summary."
            )

        self.stdout.write(
            "Matched configured model outputs, benchmark versions, and "
            "baseline summary workbook cells."
        )

    @staticmethod
    def _check_gene_results(
        *, request, storage, container_name, workbook_content, target
    ):
        tasks = dict(request.analysis_tasks.values_list("task_type", "status"))
        if tasks != {"primer": "complete", "gene": "complete"}:
            raise AssertionError(
                f"Primer and gene jobs did not both complete: {tasks!r}"
            )
        reports = json.loads(request.gene_report)
        if not any(
            name.lower().endswith((".tsv", ".csv", ".json"))
            for name in reports
        ):
            raise AssertionError("GeneSeekr produced no readable gene report.")
        expected_target = f"{target}__{target}".lower()
        identities = {}
        for hit in request.gene_results:
            if str(hit.get("target") or "").lower() != expected_target:
                continue
            try:
                identity = float(hit.get("identity"))
            except (TypeError, ValueError):
                continue
            report_key = _gene_report_key(hit.get("report")).lower()
            if report_key:
                identities[report_key] = max(
                    identity, identities.get(report_key, identity)
                )
        if not identities:
            raise AssertionError(
                f"GeneSeekr produced no normalized hit with identity for "
                f"{expected_target}."
            )
        evaluation = json.loads(
            storage.download_bytes(
                container_name, "reports/gene_category_evaluation.json"
            )
        )
        for key, actual in (
            ("normalized_hits", request.gene_results),
            ("categories", request.gene_exclusion_results),
            ("category_details", request.gene_exclusion_details),
            ("gene_reports", reports),
        ):
            if evaluation.get(key) != actual:
                raise AssertionError(
                    f"Gene evaluation {key} differs from stored results."
                )
        evidence_count = sum(
            len(items)
            for targets in request.gene_exclusion_details.values()
            for items in targets.values()
        )
        tsv = storage.download_bytes(
            container_name, "reports/gene_category_evaluation.tsv"
        ).decode("utf-8")
        rows = list(csv.reader(StringIO(tsv), delimiter="\t"))
        if (
            not rows
            or rows[0][:3] != ["report", "target", "category"]
            or len(rows) - 1 != evidence_count
        ):
            raise AssertionError(
                "Gene evaluation TSV does not match stored evidence."
            )
        workbook = load_workbook(
            BytesIO(workbook_content), read_only=True, data_only=True
        )
        try:
            results = workbook["PrimerVerifierResults"].iter_rows(values_only=True)
            headers = next(results)
            column_name = f"Gene Sequence Percent Match  {target}"
            if headers.count(column_name) != 1:
                raise AssertionError(f"Missing gene match column {column_name!r}.")
            index = headers.index(column_name)
            if headers.count("SEQID") != 1:
                raise AssertionError("Workbook is missing the SEQID column.")
            seqid_index = headers.index("SEQID")
            if not any(
                isinstance(row[index], (int, float))
                and _gene_report_key(row[seqid_index]).lower() in identities
                and abs(
                    row[index]
                    - identities[_gene_report_key(row[seqid_index]).lower()]
                ) < 0.0001
                for row in results
            ):
                raise AssertionError(
                    "Workbook has no gene match consistent with a stored hit."
                )
            audit_rows = workbook["GeneCategoryAudit"].max_row - 1
            if audit_rows != evidence_count:
                raise AssertionError(
                    "Workbook gene audit row count differs from stored evidence."
                )
        finally:
            workbook.close()
