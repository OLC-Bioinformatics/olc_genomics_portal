"""Apply the authenticated user's saved language to each request."""

import logging

from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import translate_url
from django.utils import translation

logger = logging.getLogger(__name__)


class AccountLanguageMiddleware:
    """Make an authenticated user's saved language authoritative."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.languages = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            language = user.language
            if language in self.languages:
                path_language = translation.get_language_from_path(
                    request.path_info
                )
                if path_language and path_language != language:
                    full_path = request.get_full_path()
                    translated_path = translate_url(full_path, language)
                    if translated_path == full_path:
                        path, separator, query = full_path.partition("?")
                        old_prefix = f"/{path_language}"
                        if path == old_prefix or path.startswith(
                            f"{old_prefix}/"
                        ):
                            translated_path = (
                                f"/{language}{path[len(old_prefix):]}"
                                f"{separator}{query}"
                            )
                    if translated_path != full_path:
                        return HttpResponseRedirect(translated_path)
                translation.activate(language)
                request.LANGUAGE_CODE = language
            else:
                logger.warning(
                    "Ignoring unsupported language %r for user %s",
                    language,
                    user.pk,
                )
        return self.get_response(request)
