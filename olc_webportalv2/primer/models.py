from django.utils.translation import ugettext_lazy as _
from olc_webportalv2.users.models import User
from django.db import models

class PrimerVal(models.Model):
    date = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    result = models.FileField(blank=True)
    target = models.CharField(max_length=50, default=False)
    forward = models.CharField(max_length=50, default=False)
    reverse = models.CharField(max_length=50, default=False)
    forward_gc = models.CharField(max_length=50, default=False)
    reverse_gc = models.CharField(max_length=50, default=False)
    mism = models.CharField(max_length=50, default=False)
    hits = models.CharField(max_length=50, default=False)
    misses = models.FileField(blank=True)
    status = models.CharField(max_length=64, default='Unprocessed')


