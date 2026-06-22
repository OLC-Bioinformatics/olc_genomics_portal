"""
URL configuration for the geneseekr app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Local imports
from olc_webportalv2.geneseekr import views

app_name = "geneseekr"

# URL patterns for the geneseekr app
urlpatterns = [
    # GeneSeekr Stuff
    path(
        _("geneseekr_home/"),
        views.geneseekr_home,
        name="geneseekr_home",
    ),
    path(
        _("geneseekr_query/"),
        views.geneseekr_query,
        name="geneseekr_query",
    ),
    path(
        _("geneseekr_processing/<int:geneseekr_request_pk>/"),
        views.geneseekr_processing,
        name="geneseekr_processing",
    ),
    path(
        _("geneseekr_results/<int:geneseekr_request_pk>/"),
        views.geneseekr_results,
        name="geneseekr_results",
    ),
    path(
        _("geneseekr_name/<int:geneseekr_request_pk>/"),
        views.geneseekr_name,
        name="geneseekr_name",
    ),
    path(
        _("genus_autocompleter/"),
        views.GenusAutoCompleter.as_view(),
        name="genus_autocompleter",
    ),

    # Tree Stuff
    path(
        _("tree_home/"),
        views.tree_home,
        name="tree_home",
    ),
    path(
        _("tree_request/"),
        views.tree_request,
        name="tree_request",
    ),
    path(
        _("tree_result/<int:tree_request_pk>/"),
        views.tree_result,
        name="tree_result",
    ),
    path(
        _("tree_name/<int:tree_request_pk>/"),
        views.tree_name,
        name="tree_name",
    ),

    # AMR Stuff
    path(
        _("amr_home/"),
        views.amr_home,
        name="amr_home",
    ),
    path(
        _("amr_request/"),
        views.amr_request,
        name="amr_request",
    ),
    path(
        _("amr_result/<int:amr_request_pk>/"),
        views.amr_result,
        name="amr_result",
    ),
    path(
        _("amr_name/<int:amr_request_pk>/"),
        views.amr_name,
        name="amr_name",
    ),
    path(
        _("amr_detail/<int:amr_detail_pk>/"),
        views.amr_detail,
        name="amr_detail",
    ),

    # Prokka Stuff
    path(
        _("prokka_home/"),
        views.prokka_home,
        name="prokka_home",
    ),
    path(
        _("prokka_request/"),
        views.prokka_request,
        name="prokka_request",
    ),
    path(
        _("prokka_result/<int:prokka_request_pk>/"),
        views.prokka_result,
        name="prokka_result",
    ),
    path(
        _("prokka_name/<int:prokka_request_pk>/"),
        views.prokka_name,
        name="prokka_name",
    ),

    # Nearest neighbor stuff
    path(
        _("neighbor_home/"),
        views.neighbor_home,
        name="neighbor_home",
    ),
    path(
        _("neighbor_request/"),
        views.neighbor_request,
        name="neighbor_request",
    ),
    path(
        _("neighbor_result/<int:neighbor_request_pk>/"),
        views.neighbor_result,
        name="neighbor_result",
    ),
    path(
        _("neighbor_name/<int:neighbor_request_pk>/"),
        views.neighbor_name,
        name="neighbor_name",
    ),
]
