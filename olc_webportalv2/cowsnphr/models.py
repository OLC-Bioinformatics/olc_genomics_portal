"""Database models for COWSNPhR requests and Batch task tracking."""

# Django imports
from django.contrib.postgres.fields import ArrayField
from django.db import models

# Portal-specific imports
from olc_webportalv2.users.models import User


class COWSNPhRRequest(models.Model):
    """Store request metadata, processing state, and analysis report data."""

    # Metadata information
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project_name = models.CharField(max_length=256, blank=True)
    status = models.CharField(max_length=64, default="Unprocessed")
    archive_download_link = models.CharField(
        max_length=256, blank=True, default=""
    )
    created_at = models.DateField(auto_now_add=True)
    error_list = ArrayField(
        models.CharField(max_length=256), blank=True, default=list
    )
    seqid_errors = ArrayField(
        models.CharField(max_length=256), blank=True, default=list
    )
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)
    emails_array = ArrayField(
        models.EmailField(max_length=100), blank=True, null=True, default=list
    )
    container_name = models.CharField(max_length=64, blank=True)
    # Boolean to track whether the sequence files are already uploaded
    upload = models.BooleanField(default=False)
    # SEQID analyses
    seqids = ArrayField(
        models.CharField(max_length=10000), blank=True, default=list
    )
    ref = models.CharField(max_length=64, blank=True)

    # Command line arguments
    mask_file = models.FileField(null=True, blank=True)
    # Report data
    alignment = models.TextField(blank=True, null=True)
    amino_acid_summary_table = models.JSONField(
        default=dict, blank=True, null=True
    )
    amino_acid_headers = ArrayField(
        models.CharField(max_length=256), default=list, blank=True, null=True
    )
    amino_acid_header_padding = ArrayField(
        models.IntegerField(blank=True, null=True), blank=True, null=True
    )
    assembly_report = models.JSONField(default=dict, blank=True, null=True)
    assembly_headers = ArrayField(
        models.CharField(max_length=256), default=list, blank=True, null=True
    )
    contig_summary = models.JSONField(default=dict, blank=True, null=True)
    contig_headers = ArrayField(
        models.CharField(max_length=256), default=list, blank=True, null=True
    )
    nucleotide_summary_table = models.JSONField(
        default=dict, blank=True, null=True
    )
    nucleotide_headers = ArrayField(
        models.CharField(max_length=256), default=list, blank=True, null=True
    )
    nucleotide_header_padding = ArrayField(
        models.IntegerField(blank=True, null=True), blank=True, null=True
    )
    phylogenetic_tree = models.TextField(blank=True, null=True)
    snv_matrix = models.JSONField(default=dict, blank=True, null=True)
    snv_matrix_headers = ArrayField(
        models.CharField(max_length=256), default=list, blank=True, null=True
    )
    snv_summary = models.JSONField(default=dict, blank=True, null=True)
    snv_summary_headers = ArrayField(
        models.CharField(max_length=256), default=list, blank=True, null=True
    )

    def __str__(self):
        """Return a stable display name for the analysis request."""
        return "cowsnphr-" + str(self.pk)


class ContainerName(models.Model):
    """Store a blob container name for the autocomplete form."""

    container_name = models.CharField(max_length=64, unique=True)

    def __str__(self):
        """Return the stored blob container name."""
        return self.container_name


class COWSNPhRAzureTask(models.Model):
    """Store a COWSNPhR analysis submitted to Batch."""

    cowsnphr = models.ForeignKey(
        COWSNPhRRequest, on_delete=models.CASCADE, related_name="azuretask"
    )
    exit_code_file = models.CharField(max_length=256)
