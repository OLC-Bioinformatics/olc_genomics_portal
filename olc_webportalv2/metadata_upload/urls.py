"""
URL configuration for the metadata_upload app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Local imports
from olc_webportalv2.metadata_upload import views

app_name = "metadata_upload"

# URL patterns for the metadata_upload app
urlpatterns = [
    path(
        "",
        views.upload_files,
        name="upload_files",
    ),
    path(
        _("upload_success/"),
        views.upload_success,
        name="upload_success",
    ),
]
