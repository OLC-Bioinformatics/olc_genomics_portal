"""
Create URLs for the FileZone app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Third party imports
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.filezone import views

app_name = "filezone"

# URL patterns for the FileZone app
urlpatterns = [
    path(
        "",
        views.filezone_home,
        name="filezone",
    ),
    path(
        _("container_select/"),
        views.container_select,
        name="container_select",
    ),
    path(
        _("container_create/"),
        views.container_create,
        name="container_create",
    ),
    path(
        _("container_view/<int:filezone_pk>/"),
        views.container_view,
        name="container_view",
    ),
    path(
        _("file_select/"),
        views.file_select,
        name="file_select",
    ),
    path(
        _("filezone_processing/<int:filezone_pk>/"),
        views.filezone_processing,
        name="filezone_processing",
    ),
    path(
        _("file_view/<int:filezone_pk>/"),
        views.file_view,
        name="file_view",
    ),

    # Allow user to refresh containers for autocomplete.
    path(
        _("container_refresh/"),
        views.container_refresh,
        name="refresh",
    ),

    # Views for autocompletion.
    path(
        _("container_autocompleter/"),
        views.FileZoneAutoCompleter.as_view(),
        name="filezone_autocompleter",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
