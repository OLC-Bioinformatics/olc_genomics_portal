#!/usr/bin/env python

"""Define benchmark datasets shared by FoodPort applications.

The canonical benchmark identifier is also the Azure Blob ZIP filename stem.
Keeping these values in one module prevents PrimerFinder and GeneSeekr from
using different labels or storage names for the same dataset.
"""

import re

# Azure Blob container holding the authoritative benchmark archives.
BENCHMARK_CONTAINER_NAME = "benchmark-datasets"

# Canonical identifier and human-readable label pairs. A tuple is used so the
# order remains deterministic on Python 3.5.
BENCHMARK_DATASETS = (
    ("campylobacter", "Campylobacter"),
    ("citrobacter-exclusivity", "Citrobacter-Exclusivity"),
    ("enterobacter-exclusivity", "Enterobacter-Exclusivity"),
    ("escherichia", "Escherichia"),
    ("escherichia-exclusivity", "Escherichia-Exclusivity"),
    ("listeria", "Listeria"),
    ("listeria-exclusivity", "Listeria-Exclusivity"),
    ("shigella-exclusivity", "Shigella-Exclusivity"),
    ("bds-salmonella", "BDS-Salmonella"),
    ("bds-listeria", "BDS-Listeria"),
    ("bds-stec", "BDS-STEC"),
    ("ncbi-salmonella", "NCBI-Salmonella"),
    ("ncbi-reference-exclusivity", "NCBI-Reference-Exclusivity"),
    ("vtec", "VTEC"),
    ("bds-exclusivity", "BDS-Exclusivity"),
    ("stx", "STX-Operons"),
)

# Django ChoiceField/model choices can consume this tuple directly.
BENCHMARK_CHOICES = BENCHMARK_DATASETS

# Canonical identifiers are useful for validation without rebuilding a set in
# every caller.
BENCHMARK_NAMES = frozenset(
    benchmark_name for benchmark_name, _ in BENCHMARK_DATASETS
)

# Exclusivity-only benchmarks must not be offered as inclusivity datasets.
EXCLUSIVITY_BENCHMARK_SUFFIX = "-exclusivity"
INCLUSIVITY_BENCHMARK_CHOICES = tuple(
    benchmark
    for benchmark in BENCHMARK_DATASETS
    if not benchmark[0].endswith(EXCLUSIVITY_BENCHMARK_SUFFIX)
)
EXCLUSIVITY_BENCHMARK_CHOICES = BENCHMARK_DATASETS

# Existing GeneSeekr requests may contain the previous title-case values.
# These aliases allow old requests to run without a data migration.
BENCHMARK_ALIASES = {
    "Listeria": "listeria",
    "VTEC": "vtec",
    "ncbi-ref-excl": "ncbi-reference-exclusivity",
}

# The existing request tables store panel selections in varchar(24) arrays.
BENCHMARK_PANEL_STORAGE_ALIASES = {
    "ncbi-reference-exclusivity": "ncbi-ref-excl",
}

BENCHMARK_VERSION_PATTERN = re.compile(
    r"^(?P<name>[a-z0-9-]+)-(?P<version>[0-9]+(?:\.[0-9]+)*)\.zip$"
)


def normalise_benchmark_name(*, benchmark_name):
    """Return the canonical identifier for a benchmark dataset.

    Args:
        benchmark_name (str): User selection or value stored on a request.

    Returns:
        str: Canonical lowercase benchmark identifier.

    Raises:
        ValueError: If the supplied benchmark is empty or unsupported.

    """
    if benchmark_name is None:
        raise ValueError("A benchmark dataset must be supplied.")

    benchmark_name = str(benchmark_name).strip()
    canonical_name = BENCHMARK_ALIASES.get(
        benchmark_name,
        benchmark_name.lower(),
    )

    if canonical_name not in BENCHMARK_NAMES:
        raise ValueError(f"Unsupported benchmark dataset: {benchmark_name}")

    return canonical_name


def benchmark_panel_storage_name(*, benchmark_name):
    """Return a benchmark identifier that fits the existing panel column."""
    canonical_name = normalise_benchmark_name(benchmark_name=benchmark_name)
    return BENCHMARK_PANEL_STORAGE_ALIASES.get(
        canonical_name,
        canonical_name,
    )


def benchmark_blob_name(*, benchmark_name):
    """Return the ZIP blob name for a benchmark dataset.

    Args:
        benchmark_name (str): Canonical or legacy benchmark identifier.

    Returns:
        str: Blob name in the form ``<canonical-name>.zip``.

    """
    canonical_name = normalise_benchmark_name(benchmark_name=benchmark_name)
    return f"{canonical_name}.zip"


def validate_benchmark_panel(*, benchmark_name, panel):
    """Validate a benchmark name for an inclusivity or exclusivity panel."""
    canonical_name = normalise_benchmark_name(benchmark_name=benchmark_name)
    if panel not in ("inclusivity", "exclusivity"):
        raise ValueError(f"Unsupported benchmark panel: {panel}")
    if panel == "inclusivity" and canonical_name.endswith(
        EXCLUSIVITY_BENCHMARK_SUFFIX
    ):
        raise ValueError(
            f"Benchmark dataset {canonical_name} may only be used in the "
            "exclusivity panel."
        )
    return canonical_name


def benchmark_archive_info(*, blob_name, benchmark_name=None):
    """Return the dataset and numeric version encoded in a ZIP blob name."""
    blob_name = str(blob_name or "").strip()
    match = BENCHMARK_VERSION_PATTERN.match(blob_name)
    if match:
        try:
            canonical_name = normalise_benchmark_name(
                benchmark_name=match.group("name")
            )
        except ValueError:
            return None
        if (
            benchmark_name is not None
            and canonical_name
            != normalise_benchmark_name(benchmark_name=benchmark_name)
        ):
            return None
        version = match.group("version")
        return {
            "benchmark_name": canonical_name,
            "version": version,
            "version_tuple": tuple(int(part) for part in version.split(".")),
            "blob_name": blob_name,
        }

    if benchmark_name is not None and blob_name == benchmark_blob_name(
        benchmark_name=benchmark_name
    ):
        return {
            "benchmark_name": normalise_benchmark_name(
                benchmark_name=benchmark_name
            ),
            "version": "",
            "version_tuple": (),
            "blob_name": blob_name,
        }
    return None


def select_benchmark_blob(*, blob_names, benchmark_name):
    """Select the highest semantic-versioned archive, with legacy fallback."""
    canonical_name = normalise_benchmark_name(benchmark_name=benchmark_name)
    versioned = []
    legacy_blob = benchmark_blob_name(benchmark_name=canonical_name)
    for blob_name in blob_names:
        info = benchmark_archive_info(
            blob_name=blob_name,
            benchmark_name=canonical_name,
        )
        if info and info["version"]:
            versioned.append(info)
        elif info and blob_name == legacy_blob:
            legacy_blob = info
    if versioned:
        return max(versioned, key=lambda item: item["version_tuple"])
    if isinstance(legacy_blob, dict):
        return legacy_blob
    return None
