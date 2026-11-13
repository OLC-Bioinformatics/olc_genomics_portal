"""Template filters used by COWSNPhR templates."""

from django import template
from django.utils.datastructures import MultiValueDict

register = template.Library()


@register.filter(name="handle_multivaluedict")
def handle_multivaluedict(value):
    """Format a ``MultiValueDict`` as comma-separated values.

    Args:
        value: Value to format.

    Returns:
        A comma-separated string for ``MultiValueDict`` values, or the
        original value otherwise.

    """
    if isinstance(value, MultiValueDict):
        # Convert each value list to a comma-separated string.
        return ", ".join([", ".join(v) for v in value.lists()])
    # Return the value as it is if it's not a MultiValueDict
    return value
