"""Reusable helpers for opt-in Primer Validator integration scenarios."""

import json
import re
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

INTEGRATION_DATA_DIR = Path(__file__).with_name("integration_data")
_SCENARIO_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def load_scenario(name):
    """Load a checked-in integration scenario by its safe file stem."""
    if not _SCENARIO_NAME.fullmatch(name):
        raise ValueError(f"Invalid Primer Validator scenario name: {name}")

    scenario_path = INTEGRATION_DATA_DIR / f"{name}.json"
    with scenario_path.open(encoding="utf-8") as source:
        scenario = json.load(source)
    if scenario.get("schema_version") != 1:
        raise ValueError(f"Unsupported scenario schema: {scenario_path}")
    return scenario_path, scenario


def compare_workbooks(
    *,
    expected_path,
    actual_content,
    sheet_keys,
    max_differences=20,
):
    """Compare workbook cell values, ignoring row order for keyed sheets."""
    expected_workbook = load_workbook(
        expected_path,
        read_only=True,
        data_only=True,
    )
    actual_workbook = load_workbook(
        BytesIO(actual_content),
        read_only=True,
        data_only=True,
    )
    differences = []
    difference_count = 0

    def report(message):
        nonlocal difference_count
        difference_count += 1
        if len(differences) < max_differences:
            differences.append(message)

    try:
        if expected_workbook.sheetnames != actual_workbook.sheetnames:
            report(
                "Worksheet names differ: expected "
                f"{expected_workbook.sheetnames!r}, got "
                f"{actual_workbook.sheetnames!r}"
            )

        for sheet_name in expected_workbook.sheetnames:
            if sheet_name not in actual_workbook.sheetnames:
                continue
            expected_sheet = expected_workbook[sheet_name]
            actual_sheet = actual_workbook[sheet_name]
            expected_rows = list(
                expected_sheet.iter_rows(values_only=True)
            )
            actual_rows = list(actual_sheet.iter_rows(values_only=True))
            if not expected_rows or not actual_rows:
                if expected_rows != actual_rows:
                    report(f"{sheet_name}: one workbook has no rows")
                continue

            expected_headers = expected_rows[0]
            actual_headers = actual_rows[0]
            if expected_headers != actual_headers:
                report(f"{sheet_name}: column headers differ")
                continue

            key_columns = sheet_keys.get(sheet_name)
            if key_columns:
                _compare_keyed_rows(
                    sheet_name=sheet_name,
                    headers=expected_headers,
                    expected_rows=expected_rows[1:],
                    actual_rows=actual_rows[1:],
                    key_columns=key_columns,
                    report=report,
                )
            else:
                _compare_ordered_rows(
                    sheet_name=sheet_name,
                    expected_rows=expected_rows,
                    actual_rows=actual_rows,
                    report=report,
                )
    finally:
        expected_workbook.close()
        actual_workbook.close()

    return difference_count, differences


def _compare_keyed_rows(
    *,
    sheet_name,
    headers,
    expected_rows,
    actual_rows,
    key_columns,
    report,
):
    positions = {header: index for index, header in enumerate(headers)}
    missing_columns = set(key_columns).difference(positions)
    if missing_columns:
        report(
            f"{sheet_name}: key columns are missing: "
            f"{sorted(missing_columns)!r}"
        )
        return

    key_positions = [positions[column] for column in key_columns]

    def index_rows(rows, label):
        indexed = {}
        for row in rows:
            key = tuple(row[index] for index in key_positions)
            if key in indexed:
                report(f"{sheet_name}: duplicate {label} row key {key!r}")
            else:
                indexed[key] = row
        return indexed

    expected_by_key = index_rows(expected_rows, "expected")
    actual_by_key = index_rows(actual_rows, "actual")
    for key in expected_by_key.keys() - actual_by_key.keys():
        report(f"{sheet_name}: missing result row {key!r}")
    for key in actual_by_key.keys() - expected_by_key.keys():
        report(f"{sheet_name}: unexpected result row {key!r}")

    for key in expected_by_key.keys() & actual_by_key.keys():
        expected_row = expected_by_key[key]
        actual_row = actual_by_key[key]
        for index, (expected_value, actual_value) in enumerate(
            zip(expected_row, actual_row)
        ):
            if expected_value != actual_value:
                report(
                    f"{sheet_name} row {key!r}, column "
                    f"{headers[index]!r}: expected {expected_value!r}, "
                    f"got {actual_value!r}"
                )
        if len(expected_row) != len(actual_row):
            report(f"{sheet_name} row {key!r}: column counts differ")


def _compare_ordered_rows(*, sheet_name, expected_rows, actual_rows, report):
    if len(expected_rows) != len(actual_rows):
        report(
            f"{sheet_name}: expected {len(expected_rows) - 1} data rows, "
            f"got {len(actual_rows) - 1}"
        )
    for row_index, (expected_row, actual_row) in enumerate(
        zip(expected_rows, actual_rows)
    ):
        if expected_row != actual_row:
            report(
                f"{sheet_name} row {row_index + 1}: expected "
                f"{expected_row!r}, got {actual_row!r}"
            )
