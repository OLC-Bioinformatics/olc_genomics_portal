#!/usr/bin/env python3

"""Scan Redmine for attachments missing after migration.

Compares expected attachments from the local SQLite cache
(`issue_attachments` + `id_mapping`) against current attachments on the
destination Redmine issues.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import csv
import json
import logging
import os
import sqlite3
import sys
from redminelib import Redmine

from settings import DEV_API_KEY


def setup_logging(debug):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def extension_of(filename):
    _, ext = os.path.splitext((filename or "").lower())
    return ext if ext else "[noext]"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scan migrated Redmine issues for missing attachments."
    )
    parser.add_argument(
        "--db-path",
        default="/data/web/redmine/redmine_issues.db",
        help="Path to importer SQLite database",
    )
    parser.add_argument(
        "--redmine-url",
        default="http://redmine:3000",
        help="Destination Redmine URL",
    )
    parser.add_argument(
        "--api-key",
        default=DEV_API_KEY,
        help="Redmine API key",
    )
    parser.add_argument(
        "--project-id",
        type=int,
        default=67,
        help="Legacy project id to scan (default: 67). Use 0 to scan all projects.",
    )
    parser.add_argument(
        "--compare-size",
        action="store_true",
        help="Treat same filename with mismatched file size as an issue",
    )
    parser.add_argument(
        "--limit-issues",
        type=int,
        default=0,
        help="Optional cap for number of issues to scan (0 = all)",
    )
    parser.add_argument(
        "--json-report",
        default="missing_attachments_report.json",
        help="Output JSON report path",
    )
    parser.add_argument(
        "--csv-report",
        default="missing_attachments_report.csv",
        help="Output CSV report path",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def load_expected_attachments(conn, project_id=None):
    """Load expected attachments grouped by legacy issue id."""
    conn.row_factory = sqlite3.Row
    query = """
        SELECT
            ia.issue_legacy_id,
            ia.legacy_id AS attachment_legacy_id,
            ia.filename,
            ia.filesize,
            i.project_id,
            m.new_id AS target_issue_id
        FROM issue_attachments ia
        JOIN issues i
            ON i.legacy_id = ia.issue_legacy_id
        LEFT JOIN id_mapping m
            ON m.resource = 'issues'
           AND m.legacy_id = ia.issue_legacy_id
        WHERE ia.filename IS NOT NULL
    """

    params = []
    if project_id:
        query += " AND i.project_id = ?"
        params.append(project_id)

    query += """
        ORDER BY ia.issue_legacy_id, ia.legacy_id
    """

    rows = conn.execute(query, params).fetchall()

    grouped = {}
    for row in rows:
        issue_legacy_id = row["issue_legacy_id"]
        if issue_legacy_id not in grouped:
            grouped[issue_legacy_id] = {
                "issue_legacy_id": issue_legacy_id,
                "target_issue_id": row["target_issue_id"],
                "project_id": row["project_id"],
                "attachments": [],
            }

        grouped[issue_legacy_id]["attachments"].append(
            {
                "attachment_legacy_id": row["attachment_legacy_id"],
                "filename": row["filename"],
                "filesize": row["filesize"],
            }
        )

    return grouped


def build_actual_pool(remote_issue):
    pool = defaultdict(list)
    for attachment in getattr(remote_issue, "attachments", []):
        filename = getattr(attachment, "filename", None)
        if not filename:
            continue
        pool[filename].append(getattr(attachment, "filesize", None))
    return pool


def pop_first(values, target):
    for idx, value in enumerate(values):
        if value == target:
            values.pop(idx)
            return True
    return False


def scan_issue(expected_issue, redmine, compare_size):
    result = {
        "issue_legacy_id": expected_issue["issue_legacy_id"],
        "target_issue_id": expected_issue["target_issue_id"],
        "project_id": expected_issue.get("project_id"),
        "missing": [],
        "size_mismatch": [],
        "error": None,
    }

    target_issue_id = expected_issue["target_issue_id"]
    if target_issue_id is None:
        result["error"] = "No id_mapping entry for issue"
        return result

    try:
        remote_issue = redmine.issue.get(target_issue_id, include="attachments")
    except Exception as exc:
        result["error"] = "Failed to fetch issue {0}: {1}".format(target_issue_id, exc)
        return result

    actual_pool = build_actual_pool(remote_issue)

    for expected in expected_issue["attachments"]:
        filename = expected["filename"]
        expected_size = expected["filesize"]
        available_sizes = actual_pool.get(filename, [])

        if not available_sizes:
            result["missing"].append(expected)
            continue

        if not compare_size:
            available_sizes.pop(0)
            continue

        if pop_first(available_sizes, expected_size):
            continue

        found_size = available_sizes.pop(0)
        result["size_mismatch"].append(
            {
                "attachment_legacy_id": expected["attachment_legacy_id"],
                "filename": filename,
                "expected_filesize": expected_size,
                "found_filesize": found_size,
            }
        )

    return result


def write_json_report(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def write_csv_report(path, issue_results):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "kind",
                "issue_legacy_id",
                "target_issue_id",
                "project_id",
                "attachment_legacy_id",
                "filename",
                "expected_filesize",
                "found_filesize",
                "error",
            ]
        )
        for issue in issue_results:
            issue_legacy_id = issue["issue_legacy_id"]
            target_issue_id = issue["target_issue_id"]
            project_id = issue.get("project_id")

            if issue["error"]:
                writer.writerow(
                    [
                        "issue_error",
                        issue_legacy_id,
                        target_issue_id,
                        project_id,
                        "",
                        "",
                        "",
                        "",
                        issue["error"],
                    ]
                )

            for missing in issue["missing"]:
                writer.writerow(
                    [
                        "missing",
                        issue_legacy_id,
                        target_issue_id,
                        project_id,
                        missing["attachment_legacy_id"],
                        missing["filename"],
                        missing["filesize"],
                        "",
                        "",
                    ]
                )

            for mismatch in issue["size_mismatch"]:
                writer.writerow(
                    [
                        "size_mismatch",
                        issue_legacy_id,
                        target_issue_id,
                        project_id,
                        mismatch["attachment_legacy_id"],
                        mismatch["filename"],
                        mismatch["expected_filesize"],
                        mismatch["found_filesize"],
                        "",
                    ]
                )


def main():
    args = parse_args()
    setup_logging(args.debug)

    if not os.path.isfile(args.db_path):
        logging.error("SQLite database not found: %s", args.db_path)
        sys.exit(2)

    conn = sqlite3.connect(args.db_path)
    try:
        scan_project_id = args.project_id if args.project_id and args.project_id > 0 else None
        expected_by_issue = load_expected_attachments(conn, project_id=scan_project_id)
    finally:
        conn.close()

    if not expected_by_issue:
        logging.info("No expected attachments found in %s", args.db_path)
        sys.exit(0)

    issue_rows = list(expected_by_issue.values())
    if args.limit_issues > 0:
        issue_rows = issue_rows[: args.limit_issues]

    redmine = Redmine(
        args.redmine_url,
        key=args.api_key,
        requests={"verify": False, "timeout": 60},
    )

    total_expected = 0
    total_missing = 0
    total_mismatch = 0
    issues_with_problems = 0
    issue_errors = 0
    missing_ext_counts = defaultdict(int)
    issue_results = []

    for idx, issue_row in enumerate(issue_rows, start=1):
        total_expected += len(issue_row["attachments"])

        result = scan_issue(
            expected_issue=issue_row,
            redmine=redmine,
            compare_size=args.compare_size,
        )
        issue_results.append(result)

        if result["error"]:
            issue_errors += 1

        if result["missing"] or result["size_mismatch"]:
            issues_with_problems += 1

        total_missing += len(result["missing"])
        total_mismatch += len(result["size_mismatch"])

        for missing in result["missing"]:
            missing_ext_counts[extension_of(missing["filename"])] += 1

        if idx % 250 == 0:
            logging.info(
                "Scanned %s issues with attachments (missing=%s, size_mismatch=%s, issue_errors=%s)",
                idx,
                total_missing,
                total_mismatch,
                issue_errors,
            )

    top_missing_extensions = sorted(
        missing_ext_counts.items(), key=lambda item: item[1], reverse=True
    )

    report_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "redmine_url": args.redmine_url,
        "db_path": args.db_path,
        "project_id_filter": scan_project_id,
        "compare_size": args.compare_size,
        "summary": {
            "issues_scanned": len(issue_rows),
            "issues_with_problems": issues_with_problems,
            "issue_errors": issue_errors,
            "expected_attachments": total_expected,
            "missing_attachments": total_missing,
            "size_mismatches": total_mismatch,
        },
        "missing_extensions": [
            {"extension": ext, "count": count}
            for ext, count in top_missing_extensions
        ],
        "issues": issue_results,
    }

    write_json_report(args.json_report, report_payload)
    write_csv_report(args.csv_report, issue_results)

    logging.info("Scan complete.")
    logging.info(
        "Issues scanned=%s, expected=%s, missing=%s, size_mismatch=%s, issue_errors=%s",
        len(issue_rows),
        total_expected,
        total_missing,
        total_mismatch,
        issue_errors,
    )
    logging.info("JSON report: %s", args.json_report)
    logging.info("CSV report: %s", args.csv_report)
    if top_missing_extensions:
        logging.info("Top missing extensions: %s", top_missing_extensions[:10])


if __name__ == "__main__":
    main()
