"""
This module defines the URL configurations for the Django application.

The `urlpatterns` list routes URLs to views. For more information, please see:
https://docs.djangoproject.com/en/3.1/topics/http/urls/

The `i18n_patterns` function is used to add locale prefix to the specified URL
patterns. This allows for URL translation.

In debug mode, this module also configures additional URLs for error pages and
the Django Debug Toolbar.

Imports:
- `settings`: Module that contains the settings of your Django project.
- `include`, `url`, `path`: Functions to include and define URL patterns.
- `static`: Function to serve static files during development.
- `admin`: Module that provides the administrative interface.
- `TemplateView`: Class-based view to render a given template.
- `default_views`: Module that contains default views for error handlers.
- `i18n_patterns`: Function to add locale prefix to URL patterns.

Variables:
- `urlpatterns`: A list of URL patterns for the Django application.
"""

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.utils.translation import gettext_lazy as _
from django.views import defaults as default_views
from django.views.generic import TemplateView


urlpatterns = [
    path(
        "",
        TemplateView.as_view(template_name="pages/home.html"),
        name="home",
    ),
    path(
        "about/",
        TemplateView.as_view(template_name="pages/about.html"),
        name="about",
    ),

    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin.site.urls),
]


if settings.MICROSOFT_AUTH_ENABLED:
    urlpatterns += [
        path(
            "microsoft_authentication/",
            include("microsoft_authentication.urls"),
        ),
    ]


urlpatterns += [
    path("i18n/", include("django.conf.urls.i18n")),
    path("api-auth/", include("rest_framework.urls")),
]

# API URLs are not included in the i18n_patterns, as they should not be translated.
urlpatterns += [
    path("api/", include(("olc_webportalv2.api.urls", "api"), namespace="api")),
]


# Allows for URL translation.
urlpatterns += i18n_patterns(
    path(
        _("accounts/"),
        include("allauth.urls"),
    ),
    path(
        _("ampliseq/"),
        include(
            ("olc_webportalv2.ampliseq.urls", "ampliseq"),
            namespace="ampliseq",
        ),
    ),
    path(
        _("cowbat/"),
        include(
            ("olc_webportalv2.cowbat.urls", "cowbat"),
            namespace="cowbat",
        ),
    ),
    path(
        _("cowsnphr/"),
        include(
            ("olc_webportalv2.cowsnphr.urls", "cowsnphr"),
            namespace="cowsnphr",
        ),
    ),
    path(
        _("data/"),
        include(
            ("olc_webportalv2.data.urls", "data"),
            namespace="data",
        ),
    ),
    path(
        _("filezone/"),
        include(
            ("olc_webportalv2.filezone.urls", "filezone"),
            namespace="filezone",
        ),
    ),
    path(
        _("geneseekr/"),
        include(
            ("olc_webportalv2.geneseekr.urls", "geneseekr"),
            namespace="geneseekr",
        ),
    ),
    path(
        _("metadata/"),
        include(
            ("olc_webportalv2.metadata.urls", "metadata"),
            namespace="metadata",
        ),
    ),
    path(
        _("metadata_upload/"),
        include(
            ("olc_webportalv2.metadata_upload.urls", "metadata_upload"),
            namespace="metadata_upload",
        ),
    ),
    path(
        _("primer_finder/"),
        include(
            ("olc_webportalv2.primer_finder.urls", "primer_finder"),
            namespace="primer_finder",
        ),
    ),
    path(
        _("sequence_database/"),
        include(
            ("olc_webportalv2.sequence_database.urls", "sequence_database"),
            namespace="sequence_database",
        ),
    ),
    path(
        _("users/"),
        include(
            ("olc_webportalv2.users.urls", "users"),
            namespace="users",
        ),
    ),
    path(
        _("vir_typer/"),
        include(
            ("olc_webportalv2.vir_typer.urls", "vir_typer"),
            namespace="vir_typer",
        ),
    ),
)

urlpatterns += static(
    settings.MEDIA_URL,
    document_root=settings.MEDIA_ROOT,
)


if settings.DEBUG:
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={
                "exception": Exception("Bad Request!"),
            },
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={
                "exception": Exception("Permission Denied!"),
            },
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={
                "exception": Exception("Page not Found"),
            },
        ),
        path(
            "500/",
            default_views.server_error,
        ),
    ]

    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [
            path("__debug__/", include(debug_toolbar.urls)),
        ] + urlpatterns
