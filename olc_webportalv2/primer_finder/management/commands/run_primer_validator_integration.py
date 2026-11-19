"""Run an opt-in Primer Validator workflow against a batch service."""

import hashlib
import json
import os
import time
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

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
        self._validate_fixture_files(scenario_path, expected)

        if not options["run_batch"]:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only; no request or Batch job was created."
                )
            )
            self.stdout.write(
                f"Scenario: {options['scenario']}; "
                f"samples: {len(input_data['samples'])}; "
                f"targets: {sum(len(s['targets']) for s in input_data['samples'])}; "
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
            terminal = terminal or request.status in {"Complete", "Error"}
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
                    f"Scenario matched pk473. Kept request {request.pk} "
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
                "Scenario matched pk473; the test request and output "
                "container were removed."
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
    def _wait_for_completion(*, request, timeout, poll_interval):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            request.refresh_from_db()
            if request.status == "Complete":
                return
            if request.status == "Error":
                raise RuntimeError(request.errors or "request status is Error")
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))

        request.refresh_from_db()
        raise TimeoutError(
            f"request remained {request.status!r} after {timeout} seconds"
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
        if report_hash != expected["canonical_report_sha256"]:
            raise AssertionError(
                "Raw Primer Validator report differs from the pk473 "
                f"reference: expected SHA-256 "
                f"{expected['canonical_report_sha256']}, got {report_hash}"
            )

        for model_field in ("summary", "totals"):
            expected_path = (
                scenario_path.parent / expected[f"{model_field}_file"]
            )
            expected_value = json.loads(
                expected_path.read_text(encoding="utf-8")
            )
            actual_value = json.loads(getattr(request, model_field))
            if actual_value != expected_value:
                raise AssertionError(
                    f"Stored model {model_field} differs from the pk473 "
                    "reference."
                )

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

        workbook_name = (
            f"reports/{container_name}_summary_report.xlsx"
        )
        actual_workbook = storage.download_bytes(
            container_name, workbook_name
        )
        workbook_path = (
            scenario_path.parent / expected["workbook_file"]
        )
        difference_count, differences = compare_workbooks(
            expected_path=workbook_path,
            actual_content=actual_workbook,
            sheet_keys=expected["workbook_sheets"],
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
            "Matched pk473 model outputs, raw report fingerprint, "
            "benchmark versions, and all summary workbook cells."
        )
