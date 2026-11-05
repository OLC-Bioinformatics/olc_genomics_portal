"""
URL configuration for the metadata app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Third party imports
from rest_framework.schemas import get_schema_view
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.metadata import views

app_name = "metadata"

schema_view = get_schema_view(
    title="Metadata API",
)

# URL patterns for the metadata app
urlpatterns = [
    path(
        _("metadata_home/"),
        views.metadata_home,
        name="metadata_home",
    ),
    path(
        _("metadata_results/<int:metadata_request_pk>/"),
        views.metadata_results,
        name="metadata_results",
    ),
    path(
        _("metadata_browse/"),
        views.metadata_browse,
        name="metadata_browse",
    ),
    path(
        _("metadata_submit/"),
        views.metadata_submit,
        name="metadata_submit",
    ),

    # Views for autocompletion.
    path(
        _("genus_autocompleter/"),
        views.GenusAutoCompleter.as_view(),
        name="genus_autocompleter",
    ),
    path(
        _("species_autocompleter/"),
        views.SpeciesAutoCompleter.as_view(),
        name="species_autocompleter",
    ),
    path(
        _("serotype_autocompleter/"),
        views.SerotypeAutoCompleter.as_view(),
        name="serotype_autocompleter",
    ),
    path(
        _("mlst_autocompleter/"),
        views.MLSTAutoCompleter.as_view(),
        name="mlst_autocompleter",
    ),
    path(
        _("rmlst_autocompleter/"),
        views.RMLSTAutoCompleter.as_view(),
        name="rmlst_autocompleter",
    ),

    # REST API Stuff.
    path(
        "sequencedata/",
        views.SequenceDataList.as_view(),
        name="sequencedata_list",
    ),
    path(
        "sequencedata/<int:pk>/",
        views.SequenceDataDetail.as_view(),
        name="sequencedata_detail",
    ),
    path(
        "olndata/",
        views.OLNList.as_view(),
        name="olndata_list",
    ),
    path(
        "olndata/<str:oln_id>/",
        views.OLNDetail.as_view(),
        name="olndata_detail",
    ),
    path(
        "schema/",
        schema_view,
        name="schema",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
