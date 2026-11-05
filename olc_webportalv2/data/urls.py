"""
URL configuration for the data app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Local imports
from olc_webportalv2.data import views

app_name = "data"

# URL patterns for the data app
urlpatterns = [
    path(
        "",
        views.data_home,
        name="data_home",
    ),
    path(
        _("raw_data/"),
        views.raw_data,
        name="raw_data",
    ),
    path(
        _("assembled_data/"),
        views.assembled_data,
        name="assembled_data",
    ),
    path(
        _("data_download/<int:data_request_pk>/"),
        views.data_download,
        name="data_download",
    ),
]
