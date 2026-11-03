#!/usr/bin/env python

"""
Shared benchmark dataset definitions for FoodPort applications.

The canonical benchmark identifier is also the Azure Blob ZIP filename stem.
Keeping these values in one module prevents PrimerFinder and GeneSeekr from
using different labels or storage names for the same dataset.
"""

# Azure Blob container holding the authoritative benchmark archives.
BENCHMARK_CONTAINER_NAME = 'benchmark-datasets'

# Canonical identifier and human-readable label pairs. A tuple is used so the
# order remains deterministic on Python 3.5.
BENCHMARK_DATASETS = (
    ('campylobacter', 'Campylobacter'),
    ('escherichia', 'Escherichia'),
    ('listeria', 'Listeria'),
    ('bds-salmonella', 'BDS-Salmonella'),
    ('ncbi-salmonella', 'NCBI-Salmonella'),
    ('vtec', 'VTEC'),
    ('bds-exclusivity', 'BDS-Exclusivity'),
    ('stx', 'STX-Operons'),
)

# Django ChoiceField/model choices can consume this tuple directly.
BENCHMARK_CHOICES = BENCHMARK_DATASETS

# Canonical identifiers are useful for validation without rebuilding a set in
# every caller.
BENCHMARK_NAMES = frozenset(
    benchmark_name for benchmark_name, _ in BENCHMARK_DATASETS
)

# Existing GeneSeekr requests may contain the previous title-case values.
# These aliases allow old requests to run without a data migration.
BENCHMARK_ALIASES = {
    'Listeria': 'listeria',
    'VTEC': 'vtec',
}


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
