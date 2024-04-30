from django.contrib import admin
from .models import DataFile, SequencingRun, InterOpFile, ResearchRun, SummaryMetadata, AzureTask


# Register your models here.
admin.site.register(DataFile)
admin.site.register(SequencingRun)
admin.site.register(InterOpFile)
admin.site.register(ResearchRun)
admin.site.register(SummaryMetadata)
admin.site.register(AzureTask)
