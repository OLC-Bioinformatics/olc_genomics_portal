"""Template tags for accessing dictionary keys and list positions."""

from django import template

register = template.Library()


@register.simple_tag
def access_dictionary_key(dictionary: dict, key: str):
    """Return a value from a dictionary by key.

    Args:
        dictionary: Dictionary to access.
        key: Key of the value to return.

    """
    return dictionary[key]


@register.simple_tag
def access_list_position(query_list: list, index: int):
    """Return a value from a list by index.

    Args:
        query_list: List to access.
        index: Position of the value to return.

    """
    return query_list[index]
