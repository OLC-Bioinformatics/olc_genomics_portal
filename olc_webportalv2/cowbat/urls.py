"""
URL configuration for the COWBAT app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Third party imports
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.cowbat import views

app_name = "cowbat"

# URL patterns for the COWBAT app
urlpatterns = [
    path(
        _("cowbat_processing/<int:sequencing_run_pk>/"),
        views.cowbat_processing,
        name="cowbat_processing",
    ),
    path(
        _("assembly_home/"),
        views.assembly_home,
        name="assembly_home",
    ),
    path(
        _("upload_metadata/"),
        views.upload_metadata,
        name="upload_metadata",
    ),
    path(
        _("verify_realtime/<int:sequencing_run_pk>/"),
        views.verify_realtime,
        name="verify_realtime",
    ),
    path(
        _("upload_interop/<int:sequencing_run_pk>/"),
        views.upload_interop,
        name="upload_interop",
    ),
    path(
        _("upload_sequence_data/<int:sequencing_run_pk>/"),
        views.upload_sequence_data,
        name="upload_sequence_data",
    ),
    path(
        _("retry_sequence_data_upload/<int:sequencing_run_pk>/"),
        views.retry_sequence_data_upload,
        name="retry_sequence_data_upload",
    ),
    path(
        _("assembly_results/<int:sequencing_run_pk>/"),
        views.assembly_results,
        name="assembly_results",
    ),
    path(
        _("research_assembly/"),
        views.research_assembly_home,
        name="research_assembly",
    ),
    path(
        _("blob_assembly/"),
        views.research_assembly,
        name="blob_assembly",
    ),
    path(
        _("upload_assembly/"),
        views.custom_run_request,
        name="upload_assembly",
    ),
    path(
        _("upload_assembly_upload/<int:sequencing_run_pk>/"),
        views.custom_run_upload,
        name="upload_assembly_upload",
    ),

    # Views for autocompletion.
    path(
        _("run_autocompleter/"),
        views.RunAutoCompleter.as_view(),
        name="run_autocompleter",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
