#! /usr/bin/env python

"""
Custom template filter to safely get dictionary values.
"""

# Django imports
from django import template

register = template.Library()


@register.filter
def dict_get(
    dictionary: dict,
    key: str
) -> dict:
    """
    Template to safely get a value from a dictionary
    """
    return dictionary.get(key, {})
