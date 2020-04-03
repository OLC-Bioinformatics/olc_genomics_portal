from django.contrib import admin
from olc_webportalv2.sequence_database.models import DatabaseRequest, LookupTable, NameTable, MLST, MLSTCC, RMLST, OLNID, \
    Serovar, SequenceData, Vtyper

# Register your models here.
admin.site.register(SequenceData)
admin.site.register(DatabaseRequest)
# admin.site.register(LabID)
admin.site.register(OLNID)
admin.site.register(LookupTable)
admin.site.register(NameTable)
admin.site.register(MLST)
admin.site.register(MLSTCC)
admin.site.register(RMLST)
admin.site.register(Serovar)
admin.site.register(Vtyper)
