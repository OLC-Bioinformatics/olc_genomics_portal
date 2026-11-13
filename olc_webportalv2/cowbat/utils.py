# -*- coding: utf-8 -*-

"""Normalize COWBAT and research run names."""

from __future__ import unicode_literals

import re


def normalize_run_name(raw):
    """Normalize a run name in the same way as the API serializer.

    Args:
        raw: Run name to normalize, or ``None``.

    Returns:
        A stripped, lowercase run name with underscores replaced by hyphens,
        or ``None`` when the input is ``None``.

    """
    if raw is None:
        return None
    return raw.strip().lower().replace("_", "-")


def normalize_research_run_name(raw):
    """Normalize and validate a research Blob container name.

    Args:
        raw: User-supplied research run name.

    Returns:
        A normalized Blob-compatible run name.

    Raises:
        ValueError: If the name does not match the supported format.

    """
    run_name = normalize_run_name(raw)
    if (
        not run_name
        or len(run_name) > 63
        or not re.fullmatch(
            r"[0-9]{5,6}-[a-z][a-z0-9]*(?:-[a-z0-9]+)*",
            run_name,
        )
    ):
        raise ValueError(
            "Run name must start with a five- or six-digit date followed by "
            "a lab or instrument identifier."
        )
    return run_name
