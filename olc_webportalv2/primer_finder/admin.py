# Django-related imports
from django.contrib import admin
# Primer-specific things
from .models import PrimerAzureRequest, PrimerFinder


admin.site.register(PrimerAzureRequest)
admin.site.register(PrimerFinder)

