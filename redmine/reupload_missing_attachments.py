#!/usr/bin/env python3

"""Re-upload missing Redmine attachments from scanner CSV report.

Consumes rows from missing_attachments_report.csv (kind=missing), resolves
attachment file paths from SQLite cache, and uploads only missing files to
their mapped target issues.
"""

import argparse
import csv
from collections import defaultdict
import json
import logging
import os
import sqlite3
import sys

from redminelib import Redmine
import requests

from settings import DEV_API_KEY


def setup_logging(debug):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Re-upload missing Redmine attachments from CSV report."
    )
    parser.add_argument(
        "--csv-report",
        default="/data/web/redmine/missing_attachments_report.csv",
        help="Path to CSV produced by scan_missing_attachments.py",
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
        "--attachment-root",
        default="/data/web/redmine/attachments",
        help="Root directory where exported attachments are stored",
    )
    parser.add_argument(
        "--project-id",
        type=int,
        default=67,
        help="Legacy project id to process (default 67). Use 0 for all.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of missing attachment rows to process (0=all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan uploads and validate file paths without uploading",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def resolve_local_path(local_storage_path, attachment_root, attachment_legacy_id, filename):
    if local_storage_path and os.path.isfile(local_storage_path):
        return local_storage_path

    if not attachment_root:
        return local_storage_path

    if local_storage_path:
        candidate = os.path.join(attachment_root, local_storage_path)
        if os.path.isfile(candidate):
            return candidate

    candidate = os.path.join(attachment_root, str(attachment_legacy_id), filename)
    if os.path.isfile(candidate):
        return candidate

    candidate = os.path.join(attachment_root, filename)
    if os.path.isfile(candidate):
        return candidate

    return local_storage_path


def upload_token_for_file(redmine, api_key, local_path, filename, content_type):
    token = None
    ct = content_type or "application/octet-stream"

    try:
        if hasattr(redmine, "upload") and callable(redmine.upload):
            try:
                upload_obj = redmine.upload(path=local_path, filename=filename, content_type=ct)
            except TypeError:
                with open(local_path, "rb") as handle:
                    upload_obj = redmine.upload(handle, filename=filename, content_type=ct)

            if hasattr(upload_obj, "token"):
                token = upload_obj.token
            elif isinstance(upload_obj, dict):
                token = upload_obj.get("token") or (upload_obj.get("upload", {}) or {}).get("token")
            elif isinstance(upload_obj, str):
                token = upload_obj
    except Exception as exc:
        logging.debug("redmine.upload failed for %s: %s", filename, exc)

    if token:
        return token

    headers = {"Content-Type": ct}
    if api_key:
        headers["X-Redmine-API-Key"] = api_key

    with open(local_path, "rb") as handle:
        data = handle.read()

    url = redmine.url.rstrip("/") + "/uploads.json"
    resp = requests.post(url, headers=headers, data=data, verify=False, timeout=120)
    if resp.status_code in (200, 201):
        return (resp.json().get("upload") or {}).get("token")

    if resp.status_code == 406 and ct != "application/octet-stream":
        headers["Content-Type"] = "application/octet-stream"
        resp = requests.post(url, headers=headers, data=data, verify=False, timeout=120)
        if resp.status_code in (200, 201):
            return (resp.json().get("upload") or {}).get("token")

    logging.warning("Upload token request failed for %s: %s %s", filename, resp.status_code, resp.text[:200])
    return None


def build_attachment_size_pool(remote_issue):
    pool = defaultdict(list)
    for attachment in getattr(remote_issue, "attachments", []):
        filename = getattr(attachment, "filename", None)
        if not filename:
            continue
        pool[filename].append(getattr(attachment, "filesize", None))
    return pool


def consume_attachment_match(pool, filename, filesize):
    available_sizes = pool.get(filename, [])
    if not available_sizes:
        return False

    if filesize is not None:
        for idx, value in enumerate(available_sizes):
            if value == filesize:
                available_sizes.pop(idx)
                return True

    available_sizes.pop(0)
    return True


def load_expected_issue_attachments(conn, issue_legacy_id):
    rows = conn.execute(
        """
        SELECT legacy_id, filename, filesize, content_type, description, local_storage_path
        FROM issue_attachments
        WHERE issue_legacy_id = ?
        ORDER BY legacy_id ASC
        """,
        (issue_legacy_id,),
    ).fetchall()
    return rows


def compute_missing_attachment_rows(expected_rows, remote_issue):
    remote_pool = build_attachment_size_pool(remote_issue)
    missing_rows = []

    for db_row in expected_rows:
        if consume_attachment_match(remote_pool, db_row["filename"], db_row["filesize"]):
            continue
        missing_rows.append(db_row)

    return missing_rows


def load_missing_rows(csv_path, project_id, limit):
    rows = []
    with open(csv_path, "r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("kind") != "missing":
                continue

            row_project_id = row.get("project_id")
            if project_id and project_id > 0:
                try:
                    if int(row_project_id or 0) != int(project_id):
                        continue
                except ValueError:
                    continue

            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


def main():
    args = parse_args()
    setup_logging(args.debug)

    if not os.path.isfile(args.csv_report):
        logging.error("CSV report not found: %s", args.csv_report)
        sys.exit(2)
    if not os.path.isfile(args.db_path):
        logging.error("SQLite DB not found: %s", args.db_path)
        sys.exit(2)

    missing_rows = load_missing_rows(args.csv_report, args.project_id, args.limit)
    if not missing_rows:
        logging.info("No matching missing rows found in CSV.")
        sys.exit(0)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row

    redmine = Redmine(
        args.redmine_url,
        key=args.api_key,
        requests={"verify": False, "timeout": 60},
    )

    planned = 0
    uploaded = 0
    skipped_missing_file = 0
    skipped_still_present = 0
    failed = 0

    grouped = defaultdict(list)
    for row in missing_rows:
        grouped[int(row["target_issue_id"])].append(row)

    for issue_id, issue_rows in grouped.items():
        issue_legacy_id = int(issue_rows[0]["issue_legacy_id"])
        try:
            remote_issue = redmine.issue.get(issue_id, include="attachments")
        except Exception as exc:
            logging.error("Cannot fetch target issue %s: %s", issue_id, exc)
            failed += len(issue_rows)
            continue

        expected_rows = load_expected_issue_attachments(conn, issue_legacy_id)
        if not expected_rows:
            logging.warning(
                "No SQLite attachment rows found for issue_legacy_id=%s",
                issue_legacy_id,
            )
            failed += len(issue_rows)
            continue

        rows_to_upload = compute_missing_attachment_rows(expected_rows, remote_issue)
        rows_to_upload_by_legacy_id = {
            int(db_row["legacy_id"]): db_row
            for db_row in rows_to_upload
        }

        for row in issue_rows:
            attachment_legacy_id = int(row["attachment_legacy_id"])

            planned += 1

            db_row = rows_to_upload_by_legacy_id.get(attachment_legacy_id)
            if not db_row:
                skipped_still_present += 1
                logging.debug(
                    "Skip already-present attachment issue=%s attachment_legacy_id=%s",
                    issue_id,
                    attachment_legacy_id,
                )
                continue

            filename = db_row["filename"]

            local_path = resolve_local_path(
                db_row["local_storage_path"],
                args.attachment_root,
                attachment_legacy_id,
                filename,
            )

            if not local_path or not os.path.isfile(local_path):
                skipped_missing_file += 1
                logging.warning(
                    "Local file missing for attachment_legacy_id=%s: %s",
                    attachment_legacy_id,
                    local_path,
                )
                continue

            if args.dry_run:
                logging.info(
                    "DRY RUN would upload issue=%s legacy_issue=%s file=%s path=%s",
                    issue_id,
                    issue_legacy_id,
                    filename,
                    local_path,
                )
                continue

            token = upload_token_for_file(
                redmine,
                args.api_key,
                local_path,
                filename,
                db_row["content_type"],
            )
            if not token:
                failed += 1
                continue

            try:
                redmine.issue.update(
                    issue_id,
                    uploads=[
                        {
                            "token": token,
                            "filename": filename,
                            "content_type": db_row["content_type"] or "application/octet-stream",
                            "description": db_row["description"] or "",
                        }
                    ],
                )
                uploaded += 1
                logging.info(
                    "Uploaded missing attachment issue=%s legacy_issue=%s file=%s",
                    issue_id,
                    issue_legacy_id,
                    filename,
                )
            except Exception as exc:
                failed += 1
                logging.error(
                    "Failed attaching issue=%s file=%s: %s",
                    issue_id,
                    filename,
                    exc,
                )

    conn.close()

    summary = {
        "planned_from_csv": planned,
        "uploaded": uploaded,
        "skipped_still_present": skipped_still_present,
        "skipped_missing_file": skipped_missing_file,
        "failed": failed,
        "dry_run": args.dry_run,
        "project_id_filter": args.project_id if args.project_id and args.project_id > 0 else None,
    }
    logging.info("Re-upload summary: %s", json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
