from django.contrib import admin
from .models import VirTyperAzureRequest, VirTyperFiles, VirTyperProject, VirTyperRequest, VirTyperResults

admin.site.register(VirTyperProject)
admin.site.register(VirTyperRequest)
admin.site.register(VirTyperFiles)
admin.site.register(VirTyperResults)
admin.site.register(VirTyperAzureRequest)
