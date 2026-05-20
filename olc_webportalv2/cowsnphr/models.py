# Django imports
from django.contrib.postgres.fields import (
    ArrayField,
    JSONField
)
from django.db import models

try:
    from django.utils.translation import gettext_lazy as _
except ImportError:
    from django.utils.translation import ugettext_lazy as _

# Portal-specific imports
from olc_webportalv2.users.models import User


class COWSNPhRRequest(models.Model):
    """
    Define the models for common entries and arguments
    """
    # Metadata information
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project_name = models.CharField(max_length=256, blank=True)
    status = models.CharField(max_length=64, default='Unprocessed')
    archive_download_link = models.CharField(max_length=256, blank=True, default='')
    created_at = models.DateField(auto_now_add=True)
    error_list = ArrayField(models.CharField(max_length=256), blank=True, default=list)
    seqid_errors = ArrayField(models.CharField(max_length=256), blank=True, default=list)
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)
    emails_array = ArrayField(
        models.EmailField(max_length=100),
        blank=True,
        null=True,
        default=list
    )
    container_name = models.CharField(max_length=64, blank=True)
    # Boolean to track whether the sequence files are already uploaded
    upload = models.BooleanField(default=False)
    # SEQID analyses
    seqids = ArrayField(
        models.CharField(max_length=10000), blank=True, default=list)
    ref = models.CharField(max_length=64, blank=True)

    # Command line arguments
    mask_file = models.FileField(
        null=True,
        blank=True
    )
    # Report data
    alignment = models.TextField(blank=True, null=True)
    amino_acid_summary_table = JSONField(default=dict, blank=True, null=True)
    amino_acid_headers = ArrayField(
        models.CharField(max_length=256),
        default=list,
        blank=True,
        null=True
    )
    amino_acid_header_padding = ArrayField(
        models.IntegerField(
            blank=True,
            null=True
        ),
        blank=True,
        null=True
    )
    assembly_report = JSONField(default=dict, blank=True, null=True)
    assembly_headers = ArrayField(
        models.CharField(max_length=256),
        default=list,
        blank=True,
        null=True
    )
    contig_summary = JSONField(default=dict, blank=True, null=True)
    contig_headers = ArrayField(
        models.CharField(max_length=256),
        default=list,
        blank=True,
        null=True
    )
    nucleotide_summary_table = JSONField(default=dict, blank=True, null=True)
    nucleotide_headers = ArrayField(
        models.CharField(max_length=256),
        default=list,
        blank=True,
        null=True
    )
    nucleotide_header_padding = ArrayField(
        models.IntegerField(
            blank=True,
            null=True
        ),
        blank=True,
        null=True
    )
    phylogenetic_tree = models.TextField(blank=True, null=True)
    snv_matrix = JSONField(default=dict, blank=True, null=True)
    snv_matrix_headers = ArrayField(
        models.CharField(max_length=256),
        default=list,
        blank=True,
        null=True
    )
    snv_summary = JSONField(default=dict, blank=True, null=True)
    snv_summary_headers = ArrayField(
        models.CharField(max_length=256),
        default=list,
        blank=True,
        null=True
    )

    def __str__(self):
        return 'cowsnphr-' + str(self.pk)


class ContainerName(models.Model):
    container_name = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.container_name


class COWSNPhRAzureTask(models.Model):
    """
    Class to store COWSNPhR analyses submitted to batch
    """
    cowsnphr = models.ForeignKey(
        COWSNPhRRequest,
        on_delete=models.CASCADE,
        related_name='azuretask'
    )
    exit_code_file = models.CharField(max_length=256)
