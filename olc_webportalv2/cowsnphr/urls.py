"""
Create URLs for the COWSNPhR app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Third party imports
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.cowsnphr import views

app_name = "cowsnphr"

# URL patterns for the COWSNPhR app
urlpatterns = [
    path(
        "",
        views.cowsnphr_home,
        name="cowsnphr",
    ),
    path(
        _("blob_cowsnphr/"),
        views.blob_cowsnphr,
        name="blob_cowsnphr",
    ),
    path(
        _("upload_cowsnphr/"),
        views.upload_cowsnphr,
        name="upload_cowsnphr",
    ),
    path(
        _("seqid_cowsnphr/"),
        views.seqid_cowsnphr,
        name="seqid_cowsnphr",
    ),
    path(
        _("upload_cowsnphr_files/<int:cowsnphr_pk>/"),
        views.upload_cowsnphr_files,
        name="upload_cowsnphr_files",
    ),
    path(
        _("cowsnphr_processing/<int:cowsnphr_pk>/"),
        views.cowsnphr_processing,
        name="cowsnphr_processing",
    ),
    path(
        _("cowsnphr_reports/<int:cowsnphr_pk>/"),
        views.cowsnphr_reports,
        name="cowsnphr_reports",
    ),
    path(
        _("cowsnphr_tree/<int:cowsnphr_pk>/"),
        views.cowsnphr_tree,
        name="cowsnphr_tree",
    ),
    path(
        _("cowsnphr_nucleotide_summary/<int:cowsnphr_pk>/"),
        views.cowsnphr_nucleotide_summary,
        name="cowsnphr_nucleotide_summary",
    ),
    path(
        _("cowsnphr_amino_acid_summary/<int:cowsnphr_pk>/"),
        views.cowsnphr_amino_acid_summary,
        name="cowsnphr_amino_acid_summary",
    ),

    # Allow user to refresh containers for autocomplete.
    path(
        _("container_refresh/"),
        views.container_refresh,
        name="refresh",
    ),

    # Views for autocompletion.
    path(
        _("cowsnphr_autocompleter/"),
        views.COWSNPhRAutoCompleter.as_view(),
        name="cowsnphr_autocompleter",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
