"""Template filters used by common application templates."""

from django import template
from django.utils.datastructures import MultiValueDict

register = template.Library()


@register.filter(name="handle_multivaluedict")
def handle_multivaluedict(value):
    """Format multi-valued form data as a comma-separated string.

    Args:
        value: Value to format, commonly a Django ``MultiValueDict``.

    Returns:
        A comma-separated string for multi-valued data, or the input unchanged.

    """
    if isinstance(value, MultiValueDict):
        # Flatten keys and values so repeated form entries remain visible.
        return ", ".join([", ".join(v) for v in value.lists()])
    return value
