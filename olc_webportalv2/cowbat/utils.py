# -*- coding: utf-8 -*-

"""
Normalize run names.
"""
from __future__ import unicode_literals

import re


def normalize_run_name(raw):
    """
    Normalize a run name the same way the serializer stores it.

    - Strip whitespace
    - Lowercase
    - Replace underscores with hyphens
    """
    if raw is None:
        return None
    return raw.strip().lower().replace("_", "-")


def normalize_research_run_name(raw):
    """Normalize and validate a research Blob container name."""
    run_name = normalize_run_name(raw)
    if not run_name or len(run_name) > 63 or not re.fullmatch(
        r"[0-9]{5,6}-[a-z][a-z0-9]*(?:-[a-z0-9]+)*",
        run_name,
    ):
        raise ValueError(
            "Run name must start with a five- or six-digit date followed by "
            "a lab or instrument identifier."
        )
    return run_name
