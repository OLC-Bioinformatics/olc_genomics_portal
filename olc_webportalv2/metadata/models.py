from django.db import models
from django.contrib.postgres.fields import ArrayField, JSONField
from django.utils.translation import ugettext_lazy as _

# verbose names are needed for translation!

quality_choices = (
    (_('Fail'), _('Fail')),
    (_('Pass'), _('Pass'),),
    (_('Reference'), _('Reference'))
)


class LabID(models.Model):
    labid = models.CharField(max_length=24,)

    def __str__(self):
        return self.labid


class OLNID(models.Model):
    olnid = models.CharField(max_length=24)

    def __str__(self):
        return self.olnid


class Genus(models.Model):
    genus = models.CharField(max_length=48,verbose_name=_("genus"),)

    def __str__(self):
        return self.genus


class Species(models.Model):
    species = models.CharField(max_length=48, verbose_name=_("species"),)

    def __str__(self):
        return self.species


class Serotype(models.Model):
    serotype = models.CharField(max_length=48, verbose_name=_("serotype"),)

    def __str__(self):
        return self.serotype


class MLST(models.Model):
    mlst = models.CharField(max_length=12)

    def __str__(self):
        return self.mlst


class RMLST(models.Model):
    rmlst = models.CharField(max_length=12)

    def __str__(self):
        return self.rmlst


class SequenceData(models.Model):
    seqid = models.CharField(max_length=24)
    quality = models.CharField(choices=quality_choices, max_length=128, verbose_name=_("quality"),)
    genus = models.CharField(max_length=48)
    species = models.CharField(max_length=48, blank=True)
    serotype = models.CharField(max_length=48, blank=True)
    mlst = models.CharField(max_length=12, blank=True)  # MLST is numeric, but categorical, so keep as CharField
    rmlst = models.CharField(max_length=12, blank=True)  # Same as MLST. Numeric, but categorical.
    labid = models.ForeignKey(LabID, on_delete=models.CASCADE, null=True)
    olnid = models.ForeignKey(OLNID, on_delete=models.CASCADE, null=True, related_name='seqids')

    def __str__(self):
        return self.seqid


class MetaDataRequest(models.Model):
    seqids = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    criteria = JSONField(default=dict, blank=True, null=True)

