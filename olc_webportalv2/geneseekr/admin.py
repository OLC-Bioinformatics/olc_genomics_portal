"""Django admin registrations for GeneSeekr analysis models."""

from django.contrib import admin

from olc_webportalv2.geneseekr.models import (
    AMRAzureRequest,
    AMRDetail,
    AMRSummary,
    GeneSeekrAzureRequest,
    GeneSeekrDetail,
    GeneSeekrRequest,
    NearestNeighbors,
    NearNeighborDetail,
    ProkkaAzureRequest,
    ProkkaRequest,
    TopBlastHit,
    Tree,
    TreeAzureRequest,
)

# Register your models here.
admin.site.register(GeneSeekrRequest)
admin.site.register(GeneSeekrDetail)
admin.site.register(GeneSeekrAzureRequest)
admin.site.register(TopBlastHit)
admin.site.register(TreeAzureRequest)
admin.site.register(Tree)
admin.site.register(AMRSummary)
admin.site.register(AMRAzureRequest)
admin.site.register(AMRDetail)
admin.site.register(ProkkaRequest)
admin.site.register(ProkkaAzureRequest)
admin.site.register(NearestNeighbors)
admin.site.register(NearNeighborDetail)
