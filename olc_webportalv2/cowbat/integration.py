"""Helpers for opt-in COWBAT integration scenarios."""

import csv
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from pathlib import Path

from openpyxl import load_workbook

INTEGRATION_DATA_DIR = Path(__file__).with_name("integration_data")
_SCENARIO_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def load_scenario(name):
    """Load a checked-in integration scenario by its safe file stem."""
    if not _SCENARIO_NAME.fullmatch(name):
        raise ValueError(f"Invalid COWBAT scenario name: {name}")

    scenario_path = INTEGRATION_DATA_DIR / f"{name}.json"
    with scenario_path.open(encoding="utf-8") as source:
        scenario = json.load(source)
    if scenario.get("schema_version") != 1:
        raise ValueError(f"Unsupported COWBAT scenario schema: {scenario_path}")
    return scenario_path, scenario


def integration_archive_container(run_pk):
    """Return the isolated archive container for an integration run."""
    return f"cowbat-integration-{run_pk}"


def clean_container_outputs(storage, container_name):
    """Delete every object except FASTQ inputs, tolerating an empty container."""
    for blob in storage.list_objects(container_name):
        if not blob.name.lower().endswith(".fastq.gz"):
            storage.delete_object(container_name, blob.name)

    remaining = {
        blob.name for blob in storage.list_objects(container_name)
    }
    unexpected = {
        name for name in remaining if not name.lower().endswith(".fastq.gz")
    }
    if unexpected:
        raise RuntimeError(
            "Container cleanup left non-FASTQ objects behind: "
            f"{sorted(unexpected)!r}"
        )
    return remaining


def normalize_summary_rows(rows, ignored_fields):
    """Normalize summary rows, excluding explicitly volatile fields."""
    ignored_fields = set(ignored_fields)
    normalized_rows = [
        {
            key: value
            for key, value in row.items()
            if key is not None and key not in ignored_fields
        }
        for row in rows
    ]
    return sorted(normalized_rows, key=lambda row: row.get("SeqID", ""))


def compare_numeric_rows(
    actual_rows,
    expected_rows,
    *,
    key_field,
    tolerances,
):
    """Report numeric fields outside their configured absolute tolerances."""
    differences = []

    def index_rows(rows, label):
        indexed = {}
        for row in rows:
            key = row.get(key_field)
            if key is None:
                differences.append(
                    f"{label} row is missing key field {key_field!r}"
                )
            elif key in indexed:
                differences.append(
                    f"{label} contains duplicate {key_field} {key!r}"
                )
            else:
                indexed[key] = row
        return indexed

    actual_by_key = index_rows(actual_rows, "Actual results")
    expected_by_key = index_rows(expected_rows, "Reference results")
    for key in expected_by_key.keys() - actual_by_key.keys():
        differences.append(f"Missing actual row for {key_field} {key!r}")
    for key in actual_by_key.keys() - expected_by_key.keys():
        differences.append(f"Unexpected actual row for {key_field} {key!r}")

    for key in sorted(
        expected_by_key.keys() & actual_by_key.keys(),
        key=str,
    ):
        actual = actual_by_key[key]
        expected = expected_by_key[key]
        for field, tolerance in tolerances.items():
            try:
                delta = abs(
                    Decimal(str(actual[field]))
                    - Decimal(str(expected[field]))
                )
                permitted_delta = Decimal(str(tolerance))
            except (InvalidOperation, KeyError, TypeError, ValueError):
                differences.append(
                    f"{key!r} {field}: expected numeric values, "
                    f"got {actual.get(field)!r} and {expected.get(field)!r}"
                )
                continue
            if delta > permitted_delta:
                differences.append(
                    f"{key!r} {field}: expected {expected[field]!r} "
                    f"within {tolerance} of the reference, got "
                    f"{actual[field]!r}"
                )
    return differences


def canonical_csv_sha256(content, *, ignored_fields=(), sort_columns=()):
    """Hash CSV row values independent of ordering and ignored columns."""
    reader = csv.DictReader(StringIO(content.decode("utf-8-sig")))
    if not reader.fieldnames:
        raise ValueError("CSV output has no column headers.")
    rows = list(reader)
    if not rows:
        raise ValueError("CSV output has no data rows.")
    if sort_columns:
        missing_columns = set(sort_columns) - set(reader.fieldnames)
        if missing_columns:
            raise ValueError(
                f"CSV output is missing sort columns: "
                f"{sorted(missing_columns)!r}"
            )

    ignored_fields = set(ignored_fields)
    for row in rows:
        for field in ignored_fields | {None}:
            row.pop(field, None)

    if sort_columns:
        rows.sort(
            key=lambda row: tuple(
                row.get(column, "") for column in sort_columns
            )
        )
    else:
        rows.sort(key=lambda row: json.dumps(row, sort_keys=True))
    canonical = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def combined_metadata_sha256(content, *, ignored_fields=("AssemblyDate",)):
    """Hash combined metadata semantically while ignoring volatile fields."""
    reader = csv.DictReader(StringIO(content.decode("utf-8-sig")))
    if not reader.fieldnames or "SeqID" not in reader.fieldnames:
        raise ValueError("combinedMetadata.csv has no SeqID column.")
    if "AssemblyDate" not in reader.fieldnames:
        raise ValueError("combinedMetadata.csv has no AssemblyDate column.")
    return canonical_csv_sha256(
        content,
        ignored_fields=ignored_fields,
        sort_columns=("SeqID",),
    )


def assembly_fasta_metrics(content):
    """Return contig count, total length, and N50 for a FASTA assembly."""
    lengths = []
    current_length = 0
    has_header = False
    for line in content.decode("utf-8").splitlines():
        if line.startswith(">"):
            if has_header:
                lengths.append(current_length)
            has_header = True
            current_length = 0
        elif line.strip():
            if not has_header:
                raise ValueError("FASTA sequence appeared before its header.")
            current_length += len(line.strip())

    if has_header:
        lengths.append(current_length)
    if not lengths:
        raise ValueError("FASTA assembly has no contigs.")

    total_length = sum(lengths)
    cumulative = 0
    n50 = 0
    for length in sorted(lengths, reverse=True):
        cumulative += length
        if cumulative * 2 >= total_length:
            n50 = length
            break
    return {
        "contig_count": len(lengths),
        "total_length": total_length,
        "n50": n50,
    }


def workbook_values_sha256(content):
    """Hash workbook values, ignoring nondeterministic XLSX container data."""
    workbook = load_workbook(
        BytesIO(content),
        read_only=True,
        data_only=True,
    )
    try:
        values = {
            sheet_name: [
                list(row)
                for row in workbook[sheet_name].iter_rows(values_only=True)
            ]
            for sheet_name in workbook.sheetnames
        }
    finally:
        workbook.close()
    canonical = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
