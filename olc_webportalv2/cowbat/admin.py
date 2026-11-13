"""Django admin registrations for COWBAT models."""

from django.contrib import admin

from .models import (
    AzureTask,
    DataFile,
    InterOpFile,
    ResearchRun,
    SequencingRun,
    SummaryMetadata,
)

admin.site.register(DataFile)
admin.site.register(SequencingRun)
admin.site.register(InterOpFile)
admin.site.register(ResearchRun)
admin.site.register(SummaryMetadata)
admin.site.register(AzureTask)
