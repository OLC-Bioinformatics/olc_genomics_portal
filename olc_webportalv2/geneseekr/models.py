from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.fields import JSONField
from olc_webportalv2.users.models import User


# Create your models here.
class GeneSeekrRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seqids = ArrayField(models.CharField(max_length=24), blank=True, default=[])
    missing_seqids = ArrayField(models.CharField(max_length=24), blank=True, default=[])
    query_sequence = models.CharField(max_length=10000, blank=True)
    status = models.CharField(max_length=64, default='Unprocessed')
    download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    geneseekr_type = models.CharField(max_length=48, default='BLASTN')
    # This will hold a dictionary of percent of isolates where gene/sequence was found for each gene:
    # In format (ish) {'gene1': 70, 'gene2: 80}
    geneseekr_results = JSONField(default={}, blank=True, null=True)
    gene_targets = ArrayField(models.CharField(max_length=128), blank=True, null=True, default=[])

    def __str__(self):
        return self.pk


# This model doesn't actually get used any more - to be deleted.
class AzureGeneSeekrTask(models.Model):
    geneseekr_request = models.ForeignKey(GeneSeekrRequest, on_delete=models.CASCADE, related_name='azuretask')
    exit_code_file = models.CharField(max_length=256)


class GeneSeekrDetail(models.Model):
    geneseekr_request = models.ForeignKey(GeneSeekrRequest, on_delete=models.CASCADE, related_name='geneseekrdetail', null=True)
    seqid = models.CharField(max_length=24, default='')
    # Pretty much identical to geneseekr request JSONField, but this one has percent ID for the value instead of percent
    # of times found.
    geneseekr_results = JSONField(default={}, blank=True, null=True)

    def __str__(self):
        return self.seqid


class TopBlastHit(models.Model):
    geneseekr_request = models.ForeignKey(GeneSeekrRequest, on_delete=models.CASCADE, related_name='topblasthits', null=True)
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

    # This should allow for ordering of results - when getting top blast hits associated with a GeneSeekrRequest,
    # hits should be ordered by e-value, with ties broken by percent identity and then query coverage
    # TODO: This doesn't quite seem to be working - to be investigated.
    class Meta:
        ordering = ['e_value', '-percent_identity', '-query_coverage']


class ParsnpTree(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seqids = ArrayField(models.CharField(max_length=24), blank=True, default=[])
    newick_tree = models.CharField(max_length=10000, blank=True)
    download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=64, default='Unprocessed')


class ParsnpAzureRequest(models.Model):
    tree_request = models.ForeignKey(ParsnpTree, on_delete=models.CASCADE, related_name='azuretask')
    exit_code_file = models.CharField(max_length=256)
