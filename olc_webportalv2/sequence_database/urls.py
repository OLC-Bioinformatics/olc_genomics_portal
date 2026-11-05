"""
URL configuration for the sequence_database app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Third party imports
from rest_framework.schemas import get_schema_view
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.sequence_database import views

app_name = "sequence_database"

schema_view = get_schema_view(
    title="Metadata API",
)

# URL patterns for the sequence_database app
urlpatterns = [
    path(
        "",
        views.database_home,
        name="database_home",
    ),
    path(
        _("database_filter/"),
        views.database_filter,
        name="database_filter",
    ),
    path(
        _("database_query/"),
        views.database_query,
        name="database_query",
    ),
    path(
        _("id_search/"),
        views.id_search,
        name="id_search",
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
        _("mlst_autocompleter/"),
        views.MLSTAutoCompleter.as_view(),
        name="mlst_autocompleter",
    ),
    path(
        _("mlstcc_autocompleter/"),
        views.MLSTCCAutoCompleter.as_view(),
        name="mlstcc_autocompleter",
    ),
    path(
        _("rmlst_autocompleter/"),
        views.RMLSTAutoCompleter.as_view(),
        name="rmlst_autocompleter",
    ),
    path(
        _("serovar_autocompleter/"),
        views.SerovarAutoCompleter.as_view(),
        name="serovar_autocompleter",
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
