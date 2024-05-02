from django.contrib import admin


from olc_webportalv2.cowsnphr.models import (
    COWSNPhRAzureTask,
    COWSNPhRRequest,
    ContainerName
)


# Register your models here.
admin.site.register(COWSNPhRRequest)
admin.site.register(ContainerName)
admin.site.register(COWSNPhRAzureTask)
