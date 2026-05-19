# app/templatetags/custom_filters.py
from django import template
from django.utils.datastructures import MultiValueDict

register = template.Library()

@register.filter(name='handle_multivaluedict')
def handle_multivaluedict(value):
    if isinstance(value, MultiValueDict):
        # Convert MultiValueDict to a string representation or handle it as needed
        return ', '.join([', '.join(v) for v in value.lists()])
    # Return the value as it is if it's not a MultiValueDict
    return value
