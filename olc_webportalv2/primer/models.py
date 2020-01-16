from django.utils.translation import ugettext_lazy as _
from olc_webportalv2.users.models import User
from django.db import models

class PrimerVal(models.Model):
    name = models.CharField(max_length=50)
    date = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=64, default='Unprocessed')

    path = models.FileField(blank=False)
    sequence_path = models.FileField(blank=False)
    primer_file = models.FileField(blank=False)
    mismatches = models.CharField(max_length=50, default=False)
    analysistype = models.CharField(max_length=50, default=False)
    export_amplicons = models.BooleanField(default=True)



class PrimerAzureRequest(models.Model):
    name = models.ForeignKey(PrimerVal, on_delete=models.CASCADE, related_name='primer_azure_request')
    exit_code_file = models.CharField(max_length=256, blank=False)
