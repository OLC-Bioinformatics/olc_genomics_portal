"""
URL configuration for the vir_typer app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Local imports
from olc_webportalv2.vir_typer import views

# App name for namespacing
app_name = "vir_typer"

# URL patterns for the vir_typer app
urlpatterns = [
    path(
        "",
        views.vir_typer_home,
        name="vir_typer_home"
    ),
    path(
        _("create/"),
        views.vir_typer_request,
        name="vir_typer_request"
    ),
    path(
        _("upload/<int:vir_typer_pk>/"),
        views.vir_typer_upload,
        name="vir_typer_upload"
    ),
    path(
        _("results/<int:vir_typer_pk>/"),
        views.vir_typer_results,
        name="vir_typer_results"
    ),
    path(
        _("edit/<int:vir_typer_pk>/"),
        views.vir_typer_rename,
        name="vir_typer_rename"
    ),
]