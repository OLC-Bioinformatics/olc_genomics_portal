from django.contrib import admin
from .models import ProjectMulti, Sample, SendsketchResult, GenesipprResultsSixteens, \
    GenesipprResultsGDCS, GenesipprResults, ConFindrResults, GenomeQamlResult, AMRResult

# Register your models here.
admin.site.register(ProjectMulti)
admin.site.register(Sample)
admin.site.register(SendsketchResult)
admin.site.register(GenesipprResultsGDCS)
admin.site.register(GenesipprResults)
admin.site.register(GenesipprResultsSixteens)
admin.site.register(ConFindrResults)
admin.site.register(GenomeQamlResult)
admin.site.register(AMRResult)
