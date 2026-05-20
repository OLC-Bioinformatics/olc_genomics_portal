"""
Django template filters for displaying various data formats.
"""

from django import template

register = template.Library()


@register.filter
def display_name(value):
    """
    Convert identifiers like:
      - "vtec"              -> "STEC"
      - "escherichia"       -> "Escherichia"
      - "bds-exclusivity"   -> "BDS-Exclusivity"
      - "ncbi-salmonella"   -> "NCBI-Salmonella"
    General rule:
      - single words -> Titlecase
      - hyphenated: uppercase short prefix (<=4 chars) then Titlecase the rest
    """
    if not value:
        return ""
    val = value.strip()
    norm = val.lower()

    # explicit exceptions
    if norm == "vtec":
        return "STEC"

    parts = norm.split("-")
    if len(parts) == 1:
        return val.title()

    prefix = parts[0]
    # treat short prefix as acronym
    if len(prefix) <= 4:
        prefix_display = prefix.upper()
    else:
        prefix_display = prefix.title()

    rest = "-".join([p.title() for p in parts[1:]])
    return prefix_display + "-" + rest
