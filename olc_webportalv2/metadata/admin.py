from django.contrib import admin
from olc_webportalv2.metadata.models import SequenceData, MetaDataRequest, LabID

# Register your models here.
admin.site.register(SequenceData)
admin.site.register(MetaDataRequest)
admin.site.register(LabID)

