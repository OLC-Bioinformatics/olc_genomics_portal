# -*- coding: utf-8 -*-

"""
Normalize run names.
"""
from __future__ import unicode_literals


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
