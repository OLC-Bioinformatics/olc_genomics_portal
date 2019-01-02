from django.contrib import admin
from .models import DataFile, SequencingRun, InterOpFile


# Register your models here.
admin.site.register(DataFile)
admin.site.register(SequencingRun)
admin.site.register(InterOpFile)
