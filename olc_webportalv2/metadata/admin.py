"""Admin configuration for metadata catalog models."""

from django.contrib import admin

from olc_webportalv2.metadata.models import (
    OLNID,
    LabID,
    MetaDataRequest,
    SequenceData,
)

admin.site.register(SequenceData)
admin.site.register(MetaDataRequest)
admin.site.register(LabID)
admin.site.register(OLNID)
