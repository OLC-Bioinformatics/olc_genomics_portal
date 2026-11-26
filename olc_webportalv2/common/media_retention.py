"""Dry-run-first retention for local files associated with analysis runs."""

import csv
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings

from olc_webportalv2.common.storage_client import get_storage_client

logger = logging.getLogger(__name__)

CONTAINER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
EXCLUDED_MEDIA_ROOTS = {
    "cowsnphr_archives",
    "metadata_upload",
    "primer_validator_inputs",
    "sequences",
}
EXCLUDED_MEDIA_PREFIXES = ("cowsnphr-", "data-request-", "data_request_")


@dataclass(frozen=True)
class MediaRoot:
    """Map a local run directory to the blob container that stores its files."""

    relative_path: str
    container_name: str


@dataclass
class MediaRetentionEntry:
    """Describe one file considered by the retention scan."""

    local_path: str
    container_name: str
    object_name: str
    size: int
    action: str


@dataclass
class MediaRetentionReport:
    """Summarize a dry run or an applied retention pass."""

    scanned_files: int = 0
    stale_files: int = 0
    conflicts: int = 0
    deleted_files: int = 0
    mapped_roots: int = 0
    inaccessible_paths: int = 0
    unmapped_roots: int = 0
    excluded_roots: int = 0
    bytes_eligible: int = 0
    entries: list[MediaRetentionEntry] = field(default_factory=list)

    def summary(self):
        return (
            f"scanned={self.scanned_files}, stale={self.stale_files}, "
            f"conflicts={self.conflicts}, deleted={self.deleted_files}, "
            f"mapped_roots={self.mapped_roots}, "
            f"inaccessible={self.inaccessible_paths}, "
            f"unmapped_roots={self.unmapped_roots}, "
            f"excluded_roots={self.excluded_roots}, "
            f"bytes_eligible={self.bytes_eligible}"
        )


def _normalized_container(name):
    normalized = name.lower().replace("_", "-")
    if not CONTAINER_NAME_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _is_excluded_media_root(name):
    lowered = name.lower()
    return (
        lowered in EXCLUDED_MEDIA_ROOTS
        or "archive" in lowered
        or lowered.startswith(EXCLUDED_MEDIA_PREFIXES)
    )


def build_media_roots(existing_container_names=None, media_root=None):
    """Build the allowlist of local run paths and their associated containers."""
    from olc_webportalv2.ampliseq.models import AmpliSeqRequest
    from olc_webportalv2.cowbat.models import SequencingRun
    from olc_webportalv2.cowsnphr.models import COWSNPhRRequest
    from olc_webportalv2.geneseekr.models import (
        AMRSummary,
        GeneSeekrRequest,
        NearestNeighbors,
        ProkkaRequest,
        Tree,
    )
    from olc_webportalv2.primer_finder.models import (
        PrimerVerifierRequest,
        ValidatorRequest,
    )
    from olc_webportalv2.vir_typer.models import VirTyperProject

    roots = {}

    def add_root(relative_path, container_name):
        container = _normalized_container(container_name or "")
        if container:
            key = Path(relative_path).as_posix()
            if key not in roots:
                roots[key] = container
            elif roots[key] is not None and roots[key] != container:
                roots[key] = None

    for run in SequencingRun.objects.only("run_name", "container").iterator():
        add_root(run.run_name, run.container or run.run_name)

    for request in AmpliSeqRequest.objects.only(
        "pk", "container_name"
    ).iterator():
        if request.container_name:
            add_root(request.container_name, request.container_name)

    for request in COWSNPhRRequest.objects.only(
        "pk", "container_name"
    ).iterator():
        if request.container_name:
            add_root(f"cowsnphr-{request.pk}", request.container_name)

    for model, prefix in (
        (AMRSummary, "amrsummary"),
        (GeneSeekrRequest, "geneseekr"),
        (NearestNeighbors, "neighbor"),
        (ProkkaRequest, "prokka"),
        (Tree, "tree"),
    ):
        for request in model.objects.only("pk").iterator():
            path = f"{prefix}-{request.pk}"
            add_root(path, path)

    for request in PrimerVerifierRequest.objects.only("pk").iterator():
        current_name = request.container_namer()
        add_root(current_name, current_name)
        add_root(f"primer-verifier-{request.pk}", f"primer-verifier-{request.pk}")

    for request in ValidatorRequest.objects.prefetch_related(
        "samples"
    ).iterator(chunk_size=200):
        current_name = request.container_namer()
        add_root(current_name, current_name)
        for prefix in ("primer-validator", "primer-verifier"):
            legacy_name = f"{prefix}-{request.pk}"
            add_root(legacy_name, legacy_name)

    for project in VirTyperProject.objects.only(
        "pk", "project_name"
    ).iterator():
        container_name = project.container_namer()
        add_root(container_name, container_name)
        add_root(
            Path("vir_typer", str(project.pk)).as_posix(),
            container_name,
        )

    existing_container_names = set(existing_container_names or ())
    media_root = Path(media_root or settings.MEDIA_ROOT)
    if existing_container_names:
        try:
            local_directories = [
                child
                for child in media_root.iterdir()
                if child.is_dir()
                and not child.is_symlink()
                and not _is_excluded_media_root(child.name)
            ]
        except OSError as error:
            logger.warning("Cannot list MEDIA_ROOT %s: %s", media_root, error)
            local_directories = []
        for directory in local_directories:
            normalized = _normalized_container(directory.name)
            if (
                normalized in existing_container_names
                and directory.name not in roots
            ):
                add_root(directory.name, normalized)

    return {
        relative_path: (
            MediaRoot(relative_path, container_name)
            if container_name is not None
            else None
        )
        for relative_path, container_name in roots.items()
    }


def _walk_files(root, report):
    def on_error(error):
        report.inaccessible_paths += 1
        logger.warning("Cannot inspect media path %s: %s", error.filename, error)

    for current, directories, filenames in os.walk(root, onerror=on_error):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            path = current_path / filename
            if path.is_symlink():
                continue
            try:
                path.stat()
            except OSError as error:
                report.inaccessible_paths += 1
                logger.warning("Cannot inspect media path %s: %s", path, error)
                continue
            yield path


def _write_report_csv(report, destination):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(
            (
                "local_path",
                "container",
                "object_name",
                "size_bytes",
                "action",
            )
        )
        for entry in report.entries:
            writer.writerow(
                (
                    entry.local_path,
                    entry.container_name,
                    entry.object_name,
                    entry.size,
                    entry.action,
                )
            )


def clean_local_media(
    *,
    dry_run=True,
    retention_days=None,
    media_root=None,
    roots=None,
    storage_client=None,
    report_csv=None,
):
    """Delete stale local run files without modifying blob storage."""
    media_root = Path(media_root or settings.MEDIA_ROOT)
    retention_days = (
        retention_days
        if retention_days is not None
        else getattr(settings, "MEDIA_RETENTION_DAYS", 30)
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    storage_client = storage_client or get_storage_client()
    if roots is None:
        existing_containers = {
            container.name for container in storage_client.list_containers()
        }
        roots = build_media_roots(
            existing_container_names=existing_containers,
            media_root=media_root,
        )
    report = MediaRetentionReport()
    media_root_resolved = media_root.resolve()

    mapped_roots = []
    for relative_path, media_root_record in roots.items():
        if media_root_record is None:
            report.conflicts += 1
            logger.error(
                "Skipping ambiguous media root mapping for %s",
                relative_path,
            )
            continue
        relative = Path(relative_path)
        if relative.parts and _is_excluded_media_root(relative.parts[0]):
            continue
        candidate_root = media_root / relative
        try:
            if candidate_root.is_symlink():
                report.inaccessible_paths += 1
                logger.warning("Skipping symlinked media root %s", candidate_root)
                continue
            resolved_root = candidate_root.resolve(strict=True)
        except OSError:
            continue
        if not resolved_root.is_relative_to(media_root_resolved):
            report.inaccessible_paths += 1
            logger.warning("Skipping media root outside MEDIA_ROOT: %s", candidate_root)
            continue
        if not resolved_root.is_dir():
            continue
        mapped_roots.append((resolved_root, media_root_record))
    report.mapped_roots = len(mapped_roots)

    mapped_top_level_names = {
        Path(relative_path).parts[0]
        for relative_path in roots
        if Path(relative_path).parts
    }
    try:
        for child in media_root.iterdir():
            if child.is_dir() and _is_excluded_media_root(child.name):
                report.excluded_roots += 1
            elif (
                child.is_dir()
                and child.name not in mapped_top_level_names
                and not child.is_symlink()
            ):
                report.unmapped_roots += 1
    except OSError as error:
        report.inaccessible_paths += 1
        logger.warning("Cannot list MEDIA_ROOT %s: %s", media_root, error)

    candidates = []
    for root, media_root_record in mapped_roots:
        for local_path in _walk_files(root, report):
            report.scanned_files += 1
            try:
                stat_result = local_path.stat()
            except OSError as error:
                report.inaccessible_paths += 1
                logger.warning("Cannot read media file %s: %s", local_path, error)
                continue
            modified_at = datetime.fromtimestamp(
                stat_result.st_mtime,
                timezone.utc,
            )
            if modified_at >= cutoff:
                continue
            report.stale_files += 1
            report.bytes_eligible += stat_result.st_size
            object_name = local_path.relative_to(root).as_posix()
            candidates.append(
                (
                    local_path,
                    media_root_record.container_name,
                    object_name,
                    stat_result.st_size,
                )
            )

    for local_path, container_name, object_name, size in candidates:
        action = "would_delete" if dry_run else "deleted"
        if not dry_run:
            try:
                local_path.unlink()
                report.deleted_files += 1
            except OSError as error:
                report.inaccessible_paths += 1
                action = "delete_failed"
                logger.error("Could not remove stale local file %s: %s", local_path, error)

        report.entries.append(
            MediaRetentionEntry(
                local_path=str(local_path),
                container_name=container_name,
                object_name=object_name,
                size=size,
                action=action,
            )
        )

    if report_csv:
        _write_report_csv(report, report_csv)
    return report
