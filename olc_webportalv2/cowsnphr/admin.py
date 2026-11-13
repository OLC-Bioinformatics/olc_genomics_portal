"""Admin registrations for COWSNPhR models."""

from django.contrib import admin

from olc_webportalv2.cowsnphr.models import (
    ContainerName,
    COWSNPhRAzureTask,
    COWSNPhRRequest,
)

# Register your models here.
admin.site.register(COWSNPhRRequest)
admin.site.register(ContainerName)
admin.site.register(COWSNPhRAzureTask)
