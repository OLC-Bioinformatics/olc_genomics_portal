from django.contrib import admin
from olc_webportalv2.metadata.models import SequenceData, MetaDataRequest

# Register your models here.
admin.site.register(SequenceData)
admin.site.register(MetaDataRequest)

