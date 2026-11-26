"""Shared pagination for result and request history pages."""

from typing import TypeVar

from django.core.paginator import Page, Paginator
from django.db.models import Model, QuerySet

_Model = TypeVar("_Model", bound=Model)
HISTORY_PAGE_SIZE = 25


def paginate_queryset(
    queryset: QuerySet[_Model], page_number: str | None
) -> Page:
    """Return one bounded page, clamping malformed and out-of-range numbers."""
    return Paginator(queryset, HISTORY_PAGE_SIZE).get_page(page_number)
