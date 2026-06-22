"""
Create URLs for the AmpliSeq app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Third party imports
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.ampliseq import views

app_name = "ampliseq"

# URL patterns for the AmpliSeq app
urlpatterns = [
    path(
        "",
        views.ampliseq_home,
        name="ampliseq",
    ),
    path(
        _("blob_ampliseq/"),
        views.blob_ampliseq,
        name="blob_ampliseq",
    ),
    path(
        _("upload_ampliseq/"),
        views.upload_ampliseq,
        name="upload_ampliseq",
    ),
    path(
        _("upload_ampliseq_files/<int:ampliseq_pk>/"),
        views.upload_ampliseq_files,
        name="upload_ampliseq_files",
    ),
    path(
        _("ampliseq_processing/<int:ampliseq_pk>/"),
        views.ampliseq_processing,
        name="ampliseq_processing",
    ),
    path(
        _("ampliseq_report/<int:ampliseq_pk>/"),
        views.ampliseq_report,
        name="ampliseq_report",
    ),
    path(
        _("ampliseq_timeline/<int:ampliseq_pk>/"),
        views.ampliseq_timeline,
        name="ampliseq_timeline",
    ),

    # Allow user to refresh containers for autocomplete.
    path(
        _("container_refresh/"),
        views.container_refresh,
        name="refresh",
    ),

    # Views for autocompletion.
    path(
        _("ampliseq_autocompleter/"),
        views.AmpliSeqAutoCompleter.as_view(),
        name="ampliseq_autocompleter",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
