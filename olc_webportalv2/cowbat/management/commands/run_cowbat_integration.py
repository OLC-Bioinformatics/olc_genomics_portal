"""Run an opt-in COWBAT integration scenario against a Batch service."""

import csv
import hashlib
import os
import time
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from olc_webportalv2.common.batch_client import BatchServiceError
from olc_webportalv2.common.methods import create_batch_client
from olc_webportalv2.common.storage_client import (
    StorageServiceError,
    get_storage_client,
)
from olc_webportalv2.cowbat.integration import (
    assembly_fasta_metrics,
    canonical_csv_sha256,
    clean_container_outputs,
    combined_metadata_sha256,
    compare_numeric_rows,
    integration_archive_container,
    load_scenario,
    normalize_summary_rows,
    workbook_values_sha256,
)
from olc_webportalv2.cowbat.models import (
    AzureTask,
    DataFile,
    InterOpFile,
    NanoporeFile,
    NanoporeManifest,
    NanoporeProcessingBatch,
    NanoporeRun,
    SequencingRun,
    SummaryMetadata,
)
from olc_webportalv2.cowbat.tasks import run_cowbat_batch


class Command(BaseCommand):
    help = (
        "Compare a COWBAT Batch run with a checked-in reference. Pass "
        "--run-batch and set COWBAT_INTEGRATION_ENABLED=true to submit it."
    )

    def add_arguments(self, parser):
        parser.add_argument("--scenario", default="000000-aaa")
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
            help="Keep the integration run, outputs, and isolated archive.",
        )

    def handle(self, *args, **options):
        try:
            scenario_path, scenario = load_scenario(options["scenario"])
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self._validate_timing_options(options)
        self._validate_scenario(scenario_path, scenario)
        if not options["run_batch"]:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only; no database, storage, or Batch changes "
                    "were made."
                )
            )
            self.stdout.write(
                f"Scenario: {options['scenario']}; "
                f"run: {scenario['input']['run_name']}; "
                f"samples: {len(scenario['input']['seqids'])}"
            )
            self.stdout.write(
                "For a real Batch run, set "
                "COWBAT_INTEGRATION_ENABLED=true and pass --run-batch. "
                "ENVIRONMENT=PROD also requires --allow-production."
            )
            return

        self._require_batch_opt_in(
            allow_production=options["allow_production"]
        )
        storage = get_storage_client()
        input_data = scenario["input"]
        run_name = input_data["run_name"]
        container_name = input_data["container_name"]

        existing_run = self._preflight(
            run_name=run_name,
            container_name=container_name,
            input_data=input_data,
            storage=storage,
        )
        self._verify_batch_resources_absent(run_name)

        try:
            preserved = clean_container_outputs(storage, container_name)
        except (RuntimeError, StorageServiceError) as exc:
            raise CommandError(
                f"Unable to clear old outputs from {container_name}: {exc}"
            ) from exc

        expected_fastqs = {
            item["name"] for item in input_data["fastq_files"]
        }
        if preserved != expected_fastqs:
            raise CommandError(
                f"Input FASTQ objects changed while clearing "
                f"{container_name}: expected {sorted(expected_fastqs)!r}, "
                f"got {sorted(preserved)!r}."
            )
        try:
            remaining_fastqs = {
                item.name: {
                    "size": item.size,
                    "sha256": hashlib.sha256(
                        storage.download_bytes(container_name, item.name)
                    ).hexdigest(),
                }
                for item in storage.list_objects(container_name)
                if item.name.lower().endswith(".fastq.gz")
            }
        except StorageServiceError as exc:
            raise CommandError(
                f"Unable to recheck preserved FASTQ inputs in "
                f"{container_name}: {exc}"
            ) from exc
        self._verify_fastq_identities(
            actual_fastqs=remaining_fastqs,
            input_data=input_data,
            container_name=container_name,
        )

        with transaction.atomic():
            if existing_run is not None:
                existing_run.delete()
            run = SequencingRun.objects.create(
                run_name=run_name,
                seqids=input_data["seqids"],
                status="Unprocessed",
                workflow_state="created",
                basic_assembly=input_data["basic_assembly"],
                preprocess=input_data["preprocess"],
                nextseq=input_data["nextseq"],
                realtime_strains={},
                emails_array=[],
                integration_test=True,
            )
            archive_container = integration_archive_container(run.pk)
            try:
                archive_containers = {
                    item.name for item in storage.list_containers()
                }
            except StorageServiceError as exc:
                raise CommandError(
                    f"Unable to check isolated archive container "
                    f"{archive_container}: {exc}"
                ) from exc
            if archive_container in archive_containers:
                raise CommandError(
                    f"Isolated archive container {archive_container} already "
                    "exists; refusing to overwrite it."
                )

        self.stdout.write(
            f"Created integration run {run.pk}; submitting {run_name} "
            "to the COWBAT queue."
        )
        terminal = False
        try:
            run_cowbat_batch.apply_async(
                queue="cowbat",
                args=(run.pk,),
            )
            self._wait_for_completion(
                run=run,
                timeout=options["timeout"],
                poll_interval=options["poll_interval"],
            )
            terminal = True
            self._compare_results(
                run=run,
                scenario=scenario,
                storage=storage,
            )
        except (
            AssertionError,
            CommandError,
            RuntimeError,
            StorageServiceError,
            TimeoutError,
            ValueError,
        ) as exc:
            run.refresh_from_db()
            terminal = terminal or self._is_terminal(run)
            cleanup_status = (
                "The terminal run and outputs were retained because "
                "--keep was requested."
                if terminal
                else "The run and outputs were retained because Batch work "
                "may still be active."
            )
            if terminal and not options["keep"]:
                try:
                    self._cleanup_run(
                        run=run,
                        container_name=container_name,
                        fastq_names=expected_fastqs,
                        storage=storage,
                    )
                    cleanup_status = (
                        "The terminal integration run and its outputs "
                        "were removed."
                    )
                except (CommandError, StorageServiceError) as cleanup_error:
                    cleanup_status = str(cleanup_error)
            raise CommandError(
                f"COWBAT integration scenario {options['scenario']} failed "
                f"for run {run_name}: {exc}. {cleanup_status}"
            ) from exc

        if options["keep"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Scenario matched. Kept run {run.pk}, container "
                    f"{container_name}, and archive container "
                    f"{archive_container}."
                )
            )
            return

        self._cleanup_run(
            run=run,
            container_name=container_name,
            fastq_names=expected_fastqs,
            storage=storage,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Scenario matched; generated objects and the integration "
                "database row were removed. FASTQ inputs were preserved."
            )
        )

    @staticmethod
    def _validate_timing_options(options):
        if options["timeout"] <= 0:
            raise CommandError("--timeout must be greater than zero.")
        if options["poll_interval"] <= 0:
            raise CommandError("--poll-interval must be greater than zero.")

    @staticmethod
    def _validate_scenario(scenario_path: Path, scenario):
        input_data = scenario.get("input", {})
        run_name = input_data.get("run_name")
        container_name = input_data.get("container_name")
        if not run_name or not container_name:
            raise CommandError(
                f"Scenario is missing its run or container name: "
                f"{scenario_path}"
            )
        expected_container = run_name.lower().replace("_", "-")
        if container_name != expected_container:
            raise CommandError(
                f"Scenario container must match the COWBAT run name "
                f"({expected_container!r})."
            )
        if not run_name.isascii() or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in run_name
        ):
            raise CommandError(
                f"Scenario run name is not a valid COWBAT identifier: "
                f"{run_name!r}"
            )
        if not input_data.get("seqids") or not input_data.get("fastq_files"):
            raise CommandError(
                f"Scenario must define sample IDs and FASTQ files: "
                f"{scenario_path}"
            )
        fastq_names = [
            item.get("name") for item in input_data["fastq_files"]
        ]
        if len(fastq_names) != len(set(fastq_names)):
            raise CommandError(
                f"Scenario contains duplicate FASTQ names: "
                f"{scenario_path}"
            )
        expected = scenario.get("expected", {})
        if (
            not expected.get("summary_metadata")
            or not expected.get("summary_numeric_tolerances")
            or not expected.get("required_report_files")
            or not expected.get("output_sha256")
            or not expected.get("combined_metadata_ignored_fields")
            or not expected.get("combined_metadata_sha256")
            or not expected.get("combined_metadata_metrics")
            or not expected.get("combined_metadata_tolerances")
            or not expected.get("assembly_metrics")
            or not expected.get("assembly_metric_tolerances")
            or not expected.get("canonical_report_sha256")
            or not expected.get("canonical_report_ignored_fields")
            or not expected.get("workbook_values_sha256")
        ):
            raise CommandError(
                f"Scenario is missing expected output data: {scenario_path}"
            )

    @staticmethod
    def _require_batch_opt_in(*, allow_production):
        if os.environ.get(
            "COWBAT_INTEGRATION_ENABLED", ""
        ).lower() not in {"1", "true", "yes"}:
            raise CommandError(
                "Batch submission is disabled. Set "
                "COWBAT_INTEGRATION_ENABLED=true explicitly."
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
    def _preflight(
        *,
        run_name,
        container_name,
        input_data,
        storage,
    ):
        try:
            container_names = {
                item.name for item in storage.list_containers()
            }
            if container_name not in container_names:
                raise CommandError(
                    f"Input container {container_name} does not exist."
                )
            objects = storage.list_objects(container_name)
        except StorageServiceError as exc:
            raise CommandError(
                f"Unable to inspect input container {container_name}: {exc}"
            ) from exc

        actual_fastqs = {
            item.name: {
                "size": item.size,
                "sha256": hashlib.sha256(
                    storage.download_bytes(container_name, item.name)
                ).hexdigest(),
            }
            for item in objects
            if item.name.lower().endswith(".fastq.gz")
        }
        Command._verify_fastq_identities(
            actual_fastqs=actual_fastqs,
            input_data=input_data,
            container_name=container_name,
        )

        existing_run = SequencingRun.objects.filter(
            run_name=run_name
        ).first()
        if existing_run is None:
            return None

        if (
            existing_run.workflow_state not in {"complete", "error"}
            or existing_run.status not in {"Complete", "Error", "UploadError"}
        ):
            raise CommandError(
                f"Existing run {run_name} is not terminal "
                f"(state={existing_run.workflow_state!r}, "
                f"status={existing_run.status!r}); refusing to replace it."
            )

        linked_records = {
            "AzureTask": AzureTask.objects.filter(
                sequencing_run=existing_run
            ).exists(),
            "DataFile": DataFile.objects.filter(
                sequencing_run=existing_run
            ).exists(),
            "InterOpFile": InterOpFile.objects.filter(
                sequencing_run=existing_run
            ).exists(),
            "NanoporeFile": NanoporeFile.objects.filter(
                sequencing_run=existing_run
            ).exists(),
            "NanoporeManifest": NanoporeManifest.objects.filter(
                sequencing_run=existing_run
            ).exists(),
            "NanoporeProcessingBatch": NanoporeProcessingBatch.objects.filter(
                sequencing_run=existing_run
            ).exists(),
            "NanoporeRun": NanoporeRun.objects.filter(
                sequencing_run=existing_run
            ).exists(),
        }
        present_records = sorted(
            name for name, exists in linked_records.items() if exists
        )
        if present_records:
            raise CommandError(
                f"Existing run {run_name} has linked records "
                f"{present_records!r}; refusing to delete them."
            )
        return existing_run

    @staticmethod
    def _verify_fastq_identities(*, actual_fastqs, input_data, container_name):
        expected_fastqs = {
            item["name"]: {
                "size": item["size"],
                "sha256": item["sha256"],
            }
            for item in input_data["fastq_files"]
        }
        if actual_fastqs != expected_fastqs:
            raise CommandError(
                f"Input FASTQ objects in {container_name} differ from the "
                f"reference: expected {expected_fastqs!r}, got "
                f"{actual_fastqs!r}."
            )

    @staticmethod
    def _verify_batch_resources_absent(run_name):
        client = create_batch_client()
        try:
            client.task.list(run_name)
        except BatchServiceError as exc:
            if exc.status_code != 404:
                raise CommandError(
                    f"Unable to verify that Batch job {run_name} is absent: "
                    f"{exc}"
                ) from exc
        else:
            raise CommandError(
                f"Batch job {run_name} already exists; refusing to replace "
                "or delete it."
            )

        try:
            if client.pool.exists(run_name):
                raise CommandError(
                    f"Batch pool {run_name} already exists; refusing to "
                    "replace or delete it."
                )
        except BatchServiceError as exc:
            raise CommandError(
                f"Unable to verify that Batch pool {run_name} is absent: "
                f"{exc}"
            ) from exc

    @staticmethod
    def _wait_for_completion(*, run, timeout, poll_interval):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run.refresh_from_db()
            if run.workflow_state == "complete" and run.status == "Complete":
                return
            if Command._is_terminal(run):
                raise RuntimeError(
                    f"run reached state {run.workflow_state!r}, "
                    f"status {run.status!r}, errors {run.errors!r}"
                )
            time.sleep(
                min(poll_interval, max(0, deadline - time.monotonic()))
            )

        run.refresh_from_db()
        raise TimeoutError(
            f"run remained in state {run.workflow_state!r}, "
            f"status {run.status!r} after {timeout} seconds"
        )

    @staticmethod
    def _is_terminal(run):
        return run.workflow_state in {"complete", "error"}

    def _compare_results(self, *, run, scenario, storage):
        container_name = scenario["input"]["container_name"]
        expected = scenario["expected"]
        object_records = storage.list_objects(container_name)
        objects = {item.name for item in object_records}
        required_objects = {
            f"reports/{name}" for name in expected["required_report_files"]
        }
        required_objects.update(
            item["name"] for item in scenario["input"]["fastq_files"]
        )
        missing_objects = required_objects - objects
        if missing_objects:
            raise AssertionError(
                f"Missing expected output objects: "
                f"{sorted(missing_objects)!r}"
            )

        actual_fastqs = {
            item.name: {
                "size": item.size,
                "sha256": hashlib.sha256(
                    storage.download_bytes(container_name, item.name)
                ).hexdigest(),
            }
            for item in object_records
            if item.name.lower().endswith(".fastq.gz")
        }
        self._verify_fastq_identities(
            actual_fastqs=actual_fastqs,
            input_data=scenario["input"],
            container_name=container_name,
        )

        summary = SummaryMetadata.objects.filter(
            sequencing_run=run
        ).first()
        if summary is None:
            raise AssertionError("The run has no stored summary metadata.")
        actual_summary_rows = list((summary.summary_results or {}).values())
        summary_tolerances = expected["summary_numeric_tolerances"]
        ignored_summary_fields = set(expected["ignored_summary_fields"])
        ignored_summary_fields.update(summary_tolerances)
        actual_summary = normalize_summary_rows(
            actual_summary_rows,
            ignored_summary_fields,
        )
        expected_summary = normalize_summary_rows(
            expected["summary_metadata"],
            ignored_summary_fields,
        )
        if actual_summary != expected_summary:
            raise AssertionError(
                "Stored summary metadata differs from the reference: "
                f"expected {expected_summary!r}, got {actual_summary!r}"
            )
        summary_differences = compare_numeric_rows(
            actual_summary_rows,
            expected["summary_metadata"],
            key_field="SeqID",
            tolerances=summary_tolerances,
        )
        if summary_differences:
            raise AssertionError(
                "Summary assembly metrics differ: "
                + "; ".join(summary_differences)
            )

        for name, expected_hash in expected["output_sha256"].items():
            content = storage.download_bytes(container_name, name)
            actual_hash = hashlib.sha256(content).hexdigest()
            if actual_hash != expected_hash:
                raise AssertionError(
                    f"Output {name} differs: expected SHA-256 "
                    f"{expected_hash}, got {actual_hash}"
                )

        combined_content = storage.download_bytes(
            container_name,
            "reports/combinedMetadata.csv",
        )
        combined_hash = combined_metadata_sha256(
            combined_content,
            ignored_fields=expected["combined_metadata_ignored_fields"],
        )
        if combined_hash != expected["combined_metadata_sha256"]:
            raise AssertionError(
                "Combined metadata differs: expected SHA-256 "
                f"{expected['combined_metadata_sha256']}, "
                f"got {combined_hash}"
            )
        combined_rows = list(
            csv.DictReader(
                StringIO(combined_content.decode("utf-8-sig"))
            )
        )
        expected_combined_metrics = {
            "SeqID": scenario["input"]["seqids"][0],
            **expected["combined_metadata_metrics"],
        }
        metric_differences = compare_numeric_rows(
            combined_rows,
            [expected_combined_metrics],
            key_field="SeqID",
            tolerances=expected["combined_metadata_tolerances"],
        )
        if metric_differences:
            raise AssertionError(
                "Combined metadata metrics differ: "
                + "; ".join(metric_differences)
            )

        for name, expected_hash in expected[
            "canonical_report_sha256"
        ].items():
            content = storage.download_bytes(container_name, name)
            actual_hash = canonical_csv_sha256(
                content,
                ignored_fields=expected[
                    "canonical_report_ignored_fields"
                ][name],
            )
            if actual_hash != expected_hash:
                raise AssertionError(
                    f"Report {name} differs semantically: expected "
                    f"SHA-256 {expected_hash}, got {actual_hash}"
                )

        workbook_content = storage.download_bytes(
            container_name,
            "reports/resfinder_assembled_blastn.xlsx",
        )
        workbook_hash = workbook_values_sha256(workbook_content)
        if workbook_hash != expected["workbook_values_sha256"]:
            raise AssertionError(
                "ResFinder workbook cell values differ: expected SHA-256 "
                f"{expected['workbook_values_sha256']}, got {workbook_hash}"
            )

        assembly_content = storage.download_bytes(
            container_name,
            f"BestAssemblies/{scenario['input']['seqids'][0]}.fasta",
        )
        actual_assembly_metrics = assembly_fasta_metrics(assembly_content)
        assembly_differences = compare_numeric_rows(
            [{"SeqID": "assembly", **actual_assembly_metrics}],
            [{"SeqID": "assembly", **expected["assembly_metrics"]}],
            key_field="SeqID",
            tolerances=expected["assembly_metric_tolerances"],
        )
        if assembly_differences:
            raise AssertionError(
                "Best-assembly metrics differ: "
                + "; ".join(assembly_differences)
            )

        archive_container = integration_archive_container(run.pk)
        archive_name = expected["archive_file"].format(
            container=container_name
        )
        archive_containers = {
            item.name for item in storage.list_containers()
        }
        if archive_container not in archive_containers:
            raise AssertionError(
                f"Missing isolated archive container {archive_container}."
            )
        archive_objects = {
            item.name for item in storage.list_objects(archive_container)
        }
        if archive_objects != {archive_name}:
            raise AssertionError(
                f"Unexpected archive objects in {archive_container}: "
                f"{sorted(archive_objects)!r}"
            )

        self.stdout.write(
            "Matched stored summary metadata, deterministic report "
            "checksums and values, bounded assembly metrics, required "
            "report files, and the isolated archive."
        )

    @staticmethod
    def _cleanup_run(
        *,
        run,
        container_name,
        fastq_names,
        storage,
    ):
        try:
            preserved = clean_container_outputs(storage, container_name)
            if preserved != fastq_names:
                raise CommandError(
                    f"Cleanup preserved unexpected inputs in "
                    f"{container_name}: {sorted(preserved)!r}."
                )

            archive_container = integration_archive_container(run.pk)
            archive_containers = {
                item.name for item in storage.list_containers()
            }
            if archive_container in archive_containers:
                archive_objects = {
                    item.name
                    for item in storage.list_objects(archive_container)
                }
                expected_archive = f"{container_name}.zip"
                if archive_objects - {expected_archive}:
                    raise CommandError(
                        f"Unexpected objects in isolated archive container "
                        f"{archive_container}: {sorted(archive_objects)!r}."
                    )
                storage.delete_container(archive_container)
        except (RuntimeError, StorageServiceError) as exc:
            raise CommandError(
                f"Unable to clean integration outputs for {container_name}: "
                f"{exc}. Database run {run.pk} was retained."
            ) from exc

        run.delete()
