from django.db import models
from django.contrib.postgres.fields import ArrayField


quality_choices = (
    ('Fail', 'Fail'),
    ('Pass', 'Pass'),
    ('Reference', 'Reference')
)


# Create your models here.
class SequenceData(models.Model):
    seqid = models.CharField(max_length=24)
    quality = models.CharField(choices=quality_choices, max_length=128)
    genus = models.CharField(max_length=48)

    def __str__(self):
        return self.seqid


class MetaDataRequest(models.Model):
    seqids = ArrayField(models.CharField(max_length=24), blank=True, default=[])
