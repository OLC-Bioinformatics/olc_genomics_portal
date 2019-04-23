from django.db import models
from django.contrib.postgres.fields import ArrayField


quality_choices = (
    ('Fail', 'Fail'),
    ('Pass', 'Pass'),
    ('Reference', 'Reference')
)


class LabID(models.Model):
    labid = models.CharField(max_length=24)

    def __str__(self):
        return self.labid


class SequenceData(models.Model):
    seqid = models.CharField(max_length=24)
    quality = models.CharField(choices=quality_choices, max_length=128)
    genus = models.CharField(max_length=48)
    species = models.CharField(max_length=48, blank=True)
    serotype = models.CharField(max_length=48, blank=True)
    mlst = models.CharField(max_length=12, blank=True)  # MLST is numeric, but categorical, so keep as CharField
    rmlst = models.CharField(max_length=12, blank=True)  # Same as MLST. Numeric, but categorical.
    labid = models.ForeignKey(LabID, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return self.seqid


class MetaDataRequest(models.Model):
    seqids = ArrayField(models.CharField(max_length=24), blank=True, default=[])

