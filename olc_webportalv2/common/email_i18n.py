"""Language selection for notification emails."""

import logging
from contextlib import contextmanager
from typing import Iterator

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import translation

logger = logging.getLogger(__name__)


def get_recipient_language(recipient: str) -> str:
    """Return a registered recipient's saved language or the site default."""
    supported_languages = {code for code, _name in settings.LANGUAGES}
    language = (
        get_user_model()
        .objects.filter(email__iexact=recipient)
        .values_list("language", flat=True)
        .first()
    )
    if language is None:
        return settings.LANGUAGE_CODE
    if language in supported_languages:
        return language

    logger.warning(
        "User account for notification recipient %s has unsupported language %r; "
        "using the site default.",
        recipient,
        language,
    )
    return settings.LANGUAGE_CODE


@contextmanager
def recipient_language(recipient: str) -> Iterator[None]:
    """Activate the language saved on the recipient's account, if available."""
    with translation.override(get_recipient_language(recipient)):
        yield
