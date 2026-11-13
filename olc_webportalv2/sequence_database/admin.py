"""Admin registrations for Sequence Database models."""

from django.contrib import admin

from olc_webportalv2.sequence_database.models import (
    MLST,
    MLSTCC,
    OLNID,
    RMLST,
    DatabaseRequest,
    LookupTable,
    NameTable,
    SequenceData,
    Serovar,
    Vtyper,
)

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
