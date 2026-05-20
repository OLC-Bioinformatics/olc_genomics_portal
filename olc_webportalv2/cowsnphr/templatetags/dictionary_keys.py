from django import template

register = template.Library()


@register.simple_tag
def access_dictionary_key(
    dictionary: dict,
    key: str
):
    """
    Returns the value from a dictionary for a supplied key
    :param dict dictionary: Dictionary to access
    :param str key: Key to use to access the stored value
    :return str dictionary[key]: Value of the key stored in the dictionary
    """
    return dictionary[key]

@register.simple_tag
def access_list_position(
    query_list: list,
    index: int
):
    """
    Returns the value from a list at a supplied index
    :param list query_list: List to access
    :param int index: Index position of list to access
    """
    return query_list[index]
