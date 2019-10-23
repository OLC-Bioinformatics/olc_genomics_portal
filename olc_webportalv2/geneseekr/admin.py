from django.contrib import admin
from olc_webportalv2.geneseekr.models import GeneSeekrRequest, GeneSeekrDetail, TreeAzureRequest, Tree, \
    AMRSummary, AMRAzureRequest, AMRDetail, ProkkaRequest, ProkkaAzureRequest, NearestNeighbors, NearNeighborDetail

# Register your models here.
admin.site.register(GeneSeekrRequest)
admin.site.register(GeneSeekrDetail)
admin.site.register(TreeAzureRequest)
admin.site.register(Tree)
admin.site.register(AMRSummary)
admin.site.register(AMRAzureRequest)
admin.site.register(AMRDetail)
admin.site.register(ProkkaRequest)
admin.site.register(ProkkaAzureRequest)
admin.site.register(NearestNeighbors)
admin.site.register(NearNeighborDetail)
