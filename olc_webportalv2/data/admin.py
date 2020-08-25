# Django-related imports
from django.contrib import admin
# Data-specific things
from olc_webportalv2.data.models import DataRequest


# Register your models here.
admin.site.register(DataRequest)
