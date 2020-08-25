# Django-related imports
from django.db import models
from django.contrib.postgres.fields import ArrayField
# Portal-sepcific 
from olc_webportalv2.users.models import User


# Create your models here.
class DataRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seqids = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    missing_seqids = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    status = models.CharField(max_length=64, default='Unprocessed')
    download_link = models.CharField(max_length=256, blank=True)
    request_type = models.CharField(max_length=64, blank=True)
    created_at = models.DateField(auto_now_add=True)
