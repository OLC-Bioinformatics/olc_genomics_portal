# Django-related imports
from django import forms
from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.utils.translation import ugettext_lazy as _
from django.core.validators import MaxValueValidator, MinValueValidator
# Portal-sepcific 
from olc_webportalv2.users.models import User


class PrimerFinder(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=50, blank=True, help_text='Optional')
    date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=64, default='Unprocessed')

    custom = 'custom'
    vtyper = 'vtyper'
    analysis_types = [
        (custom, _('Custom EPCR')),
        (vtyper, _('Verotoxin')),
        
    ]

    analysistype = models.CharField(max_length = 50, choices=analysis_types, default=custom, blank=False)
    ampliconsize = models.IntegerField(default=1000, validators=[MaxValueValidator(10000), MinValueValidator(1000)])
    mismatches = models.IntegerField(default=0, validators=[MaxValueValidator(3), MinValueValidator(0)])
    export_amplicons = models.BooleanField(default=True)
    # Primer_file not required for Verotoxin analysis types
    primer_file = models.FileField(blank=True)
    primer_seq = ArrayField(models.CharField(max_length=1048,blank=True))

class PrimerAzureRequest(models.Model):
    name = models.ForeignKey(PrimerFinder, on_delete=models.CASCADE, related_name='primer_azure_request')
    exit_code_file = models.CharField(max_length=256, blank=False)
