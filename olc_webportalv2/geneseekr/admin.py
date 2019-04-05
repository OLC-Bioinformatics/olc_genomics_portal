from django.contrib import admin
from olc_webportalv2.geneseekr.models import GeneSeekrRequest, GeneSeekrDetail, ParsnpAzureRequest, ParsnpTree, AMRSummary, AMRAzureRequest, AMRDetail

# Register your models here.
admin.site.register(GeneSeekrRequest)
admin.site.register(GeneSeekrDetail)
admin.site.register(ParsnpAzureRequest)
admin.site.register(ParsnpTree)
admin.site.register(AMRSummary)
admin.site.register(AMRAzureRequest)
admin.site.register(AMRDetail)

