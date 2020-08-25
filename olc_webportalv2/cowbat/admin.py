# Django-related imports
from django.contrib import admin
# COWBAT-specific things
from .models import DataFile, SequencingRun, InterOpFile


# Register your models here.
admin.site.register(DataFile)
admin.site.register(SequencingRun)
admin.site.register(InterOpFile)
