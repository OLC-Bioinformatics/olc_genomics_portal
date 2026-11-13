"""Database models for sequence metadata and query results."""

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _

# verbose names are needed for translation!

quality_choices = (
    (_('Fail'), _('Fail')),
    (
        _('Pass'),
        _('Pass'),
    ),
    (_('Reference'), _('Reference')),
)


class LabID(models.Model):
    """Store a laboratory-assigned sequence identifier."""

    labid = models.CharField(max_length=48)

    def __str__(self):
        """Return the laboratory identifier."""
        return self.labid


class OLNID(models.Model):
    """Store an OLC network identifier."""

    olnid = models.CharField(max_length=48)

    def __str__(self):
        """Return the network identifier."""
        return self.olnid


class Genus(models.Model):
    """Store a genus used in metadata search filters."""

    genus = models.CharField(
        max_length=48,
        verbose_name=_('genus'),
    )

    def __str__(self):
        """Return the genus name."""
        return self.genus


class Species(models.Model):
    """Store a species used in metadata search filters."""

    species = models.CharField(
        max_length=48,
        verbose_name=_('species'),
    )

    def __str__(self):
        """Return the species name."""
        return self.species


class Serotype(models.Model):
    """Store a serotype used in metadata search filters."""

    serotype = models.CharField(
        max_length=48,
        verbose_name=_('serotype'),
    )

    def __str__(self):
        """Return the serotype."""
        return self.serotype


class MLST(models.Model):
    """Store an MLST value used in metadata search filters."""

    mlst = models.CharField(max_length=12)

    def __str__(self):
        """Return the MLST value."""
        return self.mlst


class RMLST(models.Model):
    """Store an rMLST value used in metadata search filters."""

    rmlst = models.CharField(max_length=12)

    def __str__(self):
        """Return the rMLST value."""
        return self.rmlst


class SequenceData(models.Model):
    """Store the searchable metadata associated with a sequence ID."""

    seqid = models.CharField(max_length=24)
    subspecies = models.CharField(max_length=48, blank=True)
    quality = models.CharField(
        choices=quality_choices,
        max_length=128,
        verbose_name=_('quality'),
    )
    genus = models.CharField(max_length=48)
    species = models.CharField(max_length=48, blank=True)
    serotype = models.CharField(max_length=48, blank=True)
    # MLST is numeric, but categorical, so keep as CharField
    mlst = models.CharField(max_length=12, blank=True)
    # Same as MLST. Numeric, but categorical.
    rmlst = models.CharField(max_length=12, blank=True)
    labid = models.ForeignKey(LabID, on_delete=models.CASCADE, null=True)
    olnid = models.ForeignKey(
        OLNID, on_delete=models.CASCADE, null=True, related_name='seqids'
    )

    def __str__(self):
        """Return the sequence identifier."""
        return self.seqid


class MetaDataRequest(models.Model):
    """Store sequence IDs and criteria from a metadata search."""

    seqids = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    criteria = models.JSONField(default=dict, blank=True, null=True)
