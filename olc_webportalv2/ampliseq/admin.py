from django.contrib import admin
from .models import (
    AmpliSeqAzureTask,
    AmpliSeqRequest,
    ContainerName
)


# Register your models here.
admin.site.register(AmpliSeqRequest)
admin.site.register(ContainerName)
admin.site.register(AmpliSeqAzureTask)
