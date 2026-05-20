"""
Register template tags
"""

from django import template

# Create a new template library
register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Custom template filter to get a dictionary item with a variable key.

    Args:
        dictionary (dict): The dictionary to get the item from.
        key (str): The key to get the item with.

    Returns:
        The item from the dictionary with the given key, or None if the key is
        not in the dictionary.
    """
    # Return the item from the dictionary with the given key
    return dictionary.get(key)
