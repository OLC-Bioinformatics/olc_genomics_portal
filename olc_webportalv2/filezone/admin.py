"""Admin registrations for FileZone models."""

from django.contrib import admin

from olc_webportalv2.filezone.models import ContainerName, Regexes

admin.site.register(ContainerName)
admin.site.register(Regexes)
