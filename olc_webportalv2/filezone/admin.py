from django.contrib import admin

# Portal-specific imports
from olc_webportalv2.filezone.models import (
    # Blobs,
    Regexes,
    ContainerName
)

# Register your models here.
# admin.site.register(Blobs)
admin.site.register(ContainerName)
admin.site.register(Regexes)
