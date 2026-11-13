"""Database models for GeneSeekr and related genomics analyses."""

from django.contrib.postgres.fields import ArrayField
from django.db import models

from olc_webportalv2.users.models import User


# GeneSeekr models
class GeneSeekrRequest(models.Model):
    """Store a GeneSeekr analysis request and its result metadata."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seqids = ArrayField(
        models.CharField(max_length=64), blank=True, default=list
    )
    missing_seqids = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    query_sequence = models.CharField(max_length=10000, blank=True)
    benchmark = models.CharField(
        max_length=48, default='', blank=True, null=True
    )
    benchmark_versions = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=64, default='Unprocessed')
    download_link = models.CharField(max_length=256, blank=True)
    download_link_sequence = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    geneseekr_type = models.CharField(max_length=48, default='BLASTN')
    # Percent of isolates where each requested gene or sequence was found.
    # For example: {'gene1': 70, 'gene2': 80}.
    geneseekr_results = models.JSONField(default=dict, blank=True, null=True)
    gene_targets = ArrayField(
        models.CharField(max_length=128), blank=True, null=True, default=list
    )
    other_input_files = ArrayField(
        models.CharField(max_length=64), blank=True, default=list, null=True
    )
    name = models.CharField(max_length=50, blank=True, null=True)
    emails_array = ArrayField(
        models.EmailField(max_length=100), blank=True, null=True, default=list
    )
    error = models.CharField(max_length=10000, blank=True)
    align = models.BooleanField(default=False)
    cutoff = models.IntegerField(default=70)
    evalue = models.CharField(max_length=32, default='1E-5')
    fasta_output = models.BooleanField(default=False)
    unique = models.BooleanField(default=True)


class GeneSeekrAzureRequest(models.Model):
    """Track the Azure Batch task for a GeneSeekr request."""

    geneseekr_request = models.ForeignKey(
        GeneSeekrRequest, on_delete=models.CASCADE, related_name='azuretask'
    )
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)


class GeneSeekrDetail(models.Model):
    """Store the GeneSeekr result summary for one sequence."""

    geneseekr_request = models.ForeignKey(
        GeneSeekrRequest,
        on_delete=models.CASCADE,
        related_name='geneseekrdetail',
        null=True,
    )
    seqid = models.CharField(max_length=64, default='')
    # Similar to GeneSeekrRequest.geneseekr_results, but stores percent ID
    # instead of percent of times found.
    geneseekr_results = models.JSONField(default=dict, blank=True, null=True)

    def __str__(self):
        """Return the sequence identifier."""
        return self.seqid


class TopBlastHit(models.Model):
    """Store one high-scoring BLAST hit for a GeneSeekr request."""

    geneseekr_request = models.ForeignKey(
        GeneSeekrRequest,
        on_delete=models.CASCADE,
        related_name='topblasthits',
        null=True,
    )
    contig_name = models.CharField(max_length=128)
    query_coverage = models.FloatField()
    percent_identity = models.FloatField()
    start_position = models.IntegerField()
    end_position = models.IntegerField()
    e_value = models.FloatField()
    gene_name = models.CharField(max_length=256, blank=True, null=True)
    query_start_position = models.IntegerField(blank=True, null=True)
    query_end_position = models.IntegerField(blank=True, null=True)
    query_sequence_length = models.IntegerField(blank=True, null=True)

    # Order hits by e-value, then percent identity, then query coverage.
    # TODO: This doesn't quite seem to be working - to be investigated.
    class Meta:
        """Sort BLAST hits by significance and alignment quality."""

        ordering = ['e_value', '-percent_identity', '-query_coverage']


# Tree models
class Tree(models.Model):
    """Store input and output metadata for a tree analysis."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seqids = ArrayField(
        models.CharField(max_length=24), blank=True, default=list, null=True
    )
    other_input_files = ArrayField(
        models.CharField(max_length=64, blank=True, default=list), null=True
    )
    newick_tree = models.CharField(max_length=10000, blank=True)
    download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=64, default='Unprocessed')
    number_diversitree_strains = models.IntegerField(blank=True, null=True)
    seqids_diversitree = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )

    name = models.CharField(max_length=50, blank=True, null=True)
    emails_array = ArrayField(
        models.EmailField(max_length=100), blank=True, null=True, default=list
    )


class TreeAzureRequest(models.Model):
    """Track the Azure Batch task for a tree request."""

    tree_request = models.ForeignKey(
        Tree, on_delete=models.CASCADE, related_name='azuretask'
    )
    exit_code_file = models.CharField(max_length=256)


# AMR models
class AMRSummary(models.Model):
    """Store input and output metadata for an AMR summary."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seqids = ArrayField(
        models.CharField(max_length=24), blank=True, default=list, null=True
    )
    other_input_files = ArrayField(
        models.CharField(max_length=64, blank=True, default=list), null=True
    )
    download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=64, default='Unprocessed')

    name = models.CharField(max_length=50, blank=True, null=True)
    emails_array = ArrayField(
        models.EmailField(max_length=100), blank=True, null=True, default=list
    )


class AMRDetail(models.Model):
    """Store AMR result data for one sequence."""

    amr_request = models.ForeignKey(
        AMRSummary,
        on_delete=models.CASCADE,
        related_name='amrdetail',
        null=True,
    )
    seqid = models.CharField(max_length=24, default='')
    amr_results = models.JSONField(default=dict, blank=True, null=True)

    def __str__(self):
        """Return the sequence identifier."""
        return self.seqid


class AMRAzureRequest(models.Model):
    """Track the Azure Batch task for an AMR summary."""

    amr_request = models.ForeignKey(
        AMRSummary, on_delete=models.CASCADE, related_name='azuretask'
    )
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)


# Prokka models
class ProkkaRequest(models.Model):
    """Store input and output metadata for a Prokka annotation request."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seqids = ArrayField(
        models.CharField(max_length=24), blank=True, default=list, null=True
    )
    download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    other_input_files = ArrayField(
        models.CharField(max_length=64, blank=True, default=list), null=True
    )
    status = models.CharField(max_length=64, default='Unprocessed')

    name = models.CharField(max_length=50, blank=True, null=True)
    emails_array = ArrayField(
        models.EmailField(max_length=100), blank=True, null=True, default=list
    )


class ProkkaAzureRequest(models.Model):
    """Track the Azure Batch task for a Prokka request."""

    prokka_request = models.ForeignKey(
        ProkkaRequest, on_delete=models.CASCADE, related_name='azuretask'
    )
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)


# Nearest-neighbor models
class NearestNeighbors(models.Model):
    """Store input and output metadata for a nearest-neighbor request."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seqid = models.CharField(max_length=24, blank=True, null=True)
    uploaded_file_name = models.CharField(max_length=64, blank=True, null=True)
    number_neighbors = models.IntegerField()
    download_link = models.CharField(max_length=256, blank=True, null=True)
    name = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(
        max_length=64, default='Unprocessed', blank=True, null=True
    )
    created_at = models.DateField(auto_now_add=True, blank=True, null=True)

    emails_array = ArrayField(
        models.EmailField(max_length=100), blank=True, null=True, default=list
    )


class NearNeighborDetail(models.Model):
    """Store one nearest-neighbor sequence and its Mash distance."""

    near_neighbor_request = models.ForeignKey(
        NearestNeighbors,
        on_delete=models.CASCADE,
        related_name='neighbor_detail',
    )
    seqid = models.CharField(max_length=24)
    distance = models.FloatField()
