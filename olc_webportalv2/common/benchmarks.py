#!/usr/bin/env python

"""
Shared benchmark dataset definitions for FoodPort applications.

The canonical benchmark identifier is also the Azure Blob ZIP filename stem.
Keeping these values in one module prevents PrimerFinder and GeneSeekr from
using different labels or storage names for the same dataset.
"""

import re

# Azure Blob container holding the authoritative benchmark archives.
BENCHMARK_CONTAINER_NAME = 'benchmark-datasets'

# Canonical identifier and human-readable label pairs. A tuple is used so the
# order remains deterministic on Python 3.5.
BENCHMARK_DATASETS = (
    ('campylobacter', 'Campylobacter'),
    ('citrobacter-exclusivity', 'Citrobacter-Exclusivity'),
    ('enterobacter-exclusivity', 'Enterobacter-Exclusivity'),
    ('escherichia', 'Escherichia'),
    ('escherichia-exclusivity', 'Escherichia-Exclusivity'),
    ('listeria', 'Listeria'),
    ('listeria-exclusivity', 'Listeria-Exclusivity'),
    ('shigella-exclusivity', 'Shigella-Exclusivity'),
    ('bds-salmonella', 'BDS-Salmonella'),
    ('bds-listeria', 'BDS-Listeria'),
    ('ncbi-salmonella', 'NCBI-Salmonella'),
    ('ncbi-reference-exclusivity', 'NCBI-Reference-Exclusivity'),
    ('vtec', 'VTEC'),
    ('bds-exclusivity', 'BDS-Exclusivity'),
    ('stx', 'STX-Operons')
)

# Django ChoiceField/model choices can consume this tuple directly.
BENCHMARK_CHOICES = BENCHMARK_DATASETS

# Canonical identifiers are useful for validation without rebuilding a set in
# every caller.
BENCHMARK_NAMES = frozenset(
    benchmark_name for benchmark_name, _ in BENCHMARK_DATASETS
)

# Benchmark identifiers ending with this suffix are exclusivity-only.
EXCLUSIVITY_BENCHMARK_SUFFIX = "-exclusivity"

# Exclusivity-only benchmarks must not be offered as inclusivity datasets.
# Tuples retain deterministic ordering on Python 3.5.
INCLUSIVITY_BENCHMARK_CHOICES = tuple(
    benchmark
    for benchmark in BENCHMARK_DATASETS
    if not benchmark[0].endswith(EXCLUSIVITY_BENCHMARK_SUFFIX)
)

# Every benchmark may be used in the exclusivity panel.
EXCLUSIVITY_BENCHMARK_CHOICES = BENCHMARK_DATASETS

# Existing GeneSeekr requests may contain the previous title-case values.
# These aliases allow old requests to run without a data migration.
BENCHMARK_ALIASES = {
    'Listeria': 'listeria',
    'VTEC': 'vtec',
}

BENCHMARK_VERSION_PATTERN = re.compile(
    r'^(?P<name>[a-z0-9-]+)-(?P<version>[0-9]+(?:\.[0-9]+)*)\.zip$'
)


def normalise_benchmark_name(*, benchmark_name):
    """
    Return the canonical identifier for a benchmark dataset.

    Args:
        benchmark_name (str): User selection or value stored on a request.

    Returns:
        str: Canonical lowercase benchmark identifier.

    Raises:
        ValueError: If the supplied benchmark is empty or unsupported.
    """
    if benchmark_name is None:
        raise ValueError('A benchmark dataset must be supplied.')

    benchmark_name = str(benchmark_name).strip()
    canonical_name = BENCHMARK_ALIASES.get(
        benchmark_name,
        benchmark_name.lower(),
    )

    if canonical_name not in BENCHMARK_NAMES:
        raise ValueError(
            'Unsupported benchmark dataset: {0}'.format(benchmark_name)
        )

    return canonical_name


def is_exclusivity_benchmark(*, benchmark_name):
    """
    Return whether a benchmark is designated as exclusivity-only.

    Exclusivity-only benchmarks have canonical identifiers ending with
    ``-exclusivity``.
    """
    canonical_name = normalise_benchmark_name(benchmark_name=benchmark_name)
    return canonical_name.endswith(EXCLUSIVITY_BENCHMARK_SUFFIX)


def validate_benchmark_panel(*, benchmark_name, panel):
    """
    Validate that a benchmark may be used in the requested panel.

    Args:
        benchmark_name (str): Canonical or legacy benchmark identifier.
        panel (str): Either ``inclusivity`` or ``exclusivity``.

    Returns:
        str: The canonical benchmark identifier.

    Raises:
        ValueError: If the panel is invalid or an exclusivity-only benchmark
            is assigned to inclusivity.
    """
    canonical_name = normalise_benchmark_name(benchmark_name=benchmark_name)

    if panel not in ("inclusivity", "exclusivity"):
        raise ValueError("Unsupported benchmark panel: {0}".format(panel))

    if panel == "inclusivity" and canonical_name.endswith(EXCLUSIVITY_BENCHMARK_SUFFIX):
        raise ValueError(
            "Benchmark dataset {0} may only be used in the exclusivity panel.".format(
                canonical_name
            )
        )

    return canonical_name


def benchmark_blob_name(*, benchmark_name):
    """
    Return the ZIP blob name for a benchmark dataset.

    Args:
        benchmark_name (str): Canonical or legacy benchmark identifier.

    Returns:
        str: Blob name in the form ``<canonical-name>.zip``.
    """
    canonical_name = normalise_benchmark_name(
        benchmark_name=benchmark_name
    )
    return '{0}.zip'.format(canonical_name)


def benchmark_archive_info(*, blob_name, benchmark_name=None):
    """Return the dataset and numeric version encoded in a blob name.

    Versions may contain one or more numeric components, such as ``1.2``,
    ``1.2.0``, or ``20260813``. The final hyphen separates the dataset name
    from the version, so names such as ``ncbi-salmonella-1.2.zip`` work.
    """
    match = BENCHMARK_VERSION_PATTERN.match(str(blob_name or '').strip())
    if match:
        try:
            canonical_name = normalise_benchmark_name(
                benchmark_name=match.group('name')
            )
        except ValueError:
            return None
        if benchmark_name is not None and canonical_name != normalise_benchmark_name(
                benchmark_name=benchmark_name):
            return None
        return {
            'benchmark_name': canonical_name,
            'version': match.group('version'),
            'version_tuple': tuple(
                int(part) for part in match.group('version').split('.')
            ),
            'blob_name': str(blob_name).strip(),
        }

    if (
        benchmark_name is not None and
        str(blob_name or '').strip() == benchmark_blob_name(
            benchmark_name=benchmark_name
        )
    ):
        return {
            'benchmark_name': normalise_benchmark_name(
                benchmark_name=benchmark_name
            ),
            'version': '',
            'version_tuple': (),
            'blob_name': str(blob_name).strip(),
        }
    return None


def select_benchmark_blob(*, blob_names, benchmark_name):
    """Select the highest semantic-versioned archive, with legacy fallback."""
    canonical_name = normalise_benchmark_name(benchmark_name=benchmark_name)
    candidates = []
    legacy = benchmark_blob_name(benchmark_name=canonical_name)
    for blob_name in blob_names:
        info = benchmark_archive_info(
            blob_name=blob_name, benchmark_name=canonical_name
        )
        if info and info['version']:
            candidates.append(info)
        elif info and blob_name == legacy:
            legacy = info
    if candidates:
        return max(candidates, key=lambda item: item['version_tuple'])
    if isinstance(legacy, dict):
        return legacy
    return None
