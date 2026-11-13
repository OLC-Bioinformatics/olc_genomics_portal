"""Admin configuration for data requests."""

from django.contrib import admin

from olc_webportalv2.data.models import DataRequest

admin.site.register(DataRequest)
