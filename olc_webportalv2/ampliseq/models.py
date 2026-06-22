#!/usr/env python3

"""
Create models for the AmpliSeq app
"""
# Standard imports
import os

# Django imports
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _

# Portal-specific imports
from olc_webportalv2.users.models import User


def set_metadata_file_path(instance, filename):
    return os.path.join(instance.pk, filename)

def set_classifier_file_path(instance, filename):
    return os.path.join(instance.pk, filename)

class ContainerName(models.Model):
    container_name = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.container_name

class AmpliSeqRequest(models.Model):
    """
    Define the models for common entries and arguments
    """
    # Metadata information
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project_name = models.CharField(max_length=256, blank=True)
    status = models.CharField(max_length=64, default='Unprocessed')
    execution_report_download_link = models.CharField(max_length=512, blank=True)
    execution_trace_download_link = models.CharField(max_length=512, blank=True)
    execution_timeline_download_link = models.CharField(max_length=512, blank=True)
    results_download_link = models.CharField(max_length=512, blank=True)
    created_at = models.DateField(auto_now_add=True)
    error_list = ArrayField(models.CharField(max_length=2048), blank=True, default=list)
    exit_code_file = models.CharField(max_length=2048, blank=True, null=True)
    emails_array = ArrayField(
        models.EmailField(max_length=100),
        blank=True,
        null=True,
        default=list
    )
    # Command line arguments
    """
    source $CONDA/activate && nextflow run /opt/nf-core-ampliseq-2.6.1/workflow/ 
    -profile singularity
    -resume 
    --input $AZ_BATCH_NODE_MOUNTS_DIR/ampliseq-191224/data/raw/fastq_gz/ 
    --FW_primer CCTACGGGNGGCWGCAG 
    --RV_primer GACTACHVGGGTATCTAATCC 
    --outdir $AZ_BATCH_NODE_MOUNTS_DIR/ampliseq-191224/results/ 
    --max_ee 100000 
    --dada_ref_taxonomy silva=132 
    --exclude_taxa mitochondria,chloroplast 
    --trunclenf 277 
    --trunclenr 234
    """
    container_name = models.CharField(max_length=64, blank=True)
    forward_primer = models.CharField(max_length=64, null=True, blank=True)
    reverse_primer = models.CharField(max_length=64, null=True, blank=True)
    max_ee = models.PositiveIntegerField(null=True, blank=True, default=2)

    min_len = models.PositiveIntegerField(null=True, blank=True, default=50)
    max_len = models.PositiveIntegerField(null=True, blank=True)

    metadata = models.FileField(
        # upload_to=set_metadata_file_path,
        null=True,
        blank=True
    )

    DADA = 'dada'
    QIIME = 'qiime'
    QIIME_CUSTOM = 'qiime_custom'

    TAXONOMY = [
        (DADA, _('Train a classifier for DADA2 taxonomic assignment')),
        (QIIME, _('Train a classifier for QIIME2 taxonomic assignment')),
        (QIIME_CUSTOM, _('Use custom classifier for QIIME2 taxonomic assignment'))
    ]

    taxonomy =models.CharField(
        max_length=50,
        choices=TAXONOMY,
        default=DADA,
        null=True,
    )

    SILVA_132 = 'silva=132'
    SILVA_138 = 'silva=138'
    RDP_18 = 'rdp=18'
    DADA_MODELS = [
        (SILVA_132, _('Silva version 132')),
        (SILVA_138, _('Silva version 138')),
        (RDP_18, _('RDP version 18')),
    ]
    QIIME_MODELS = [
        (SILVA_138, _('Silva version 138')),
        (RDP_18, _('RDP version 18')),
    ]
    #-------------
    # Mutually exclusive
    dada_ref_taxonomy = models.CharField(
        max_length=50,
        choices=DADA_MODELS,
        default=SILVA_132,
        null=True,
    )
    qiime_ref_taxonomy = models.CharField(
        max_length=50,
        choices=QIIME_MODELS,
        default=SILVA_138,
        null=True
    )
    classifier = models.FileField(
        # upload_to=set_classifier_file_path,
        null=True,
        blank=True
    )
    #-------------

    exclude_taxa = models.CharField(max_length=64, null=True, blank=True)
    # Maybe make this as a checklist? mitochondria, chloroplast
    trunc_len_f = models.PositiveIntegerField(null=True, blank=True)
    trunc_len_r = models.PositiveIntegerField(null=True, blank=True)
    # Boolean to track whether the sequence files are already uploaded
    upload = models.BooleanField(default=False)

    def __str__(self):
        return 'ampliseq-' + str(self.pk)


class AmpliSeqAzureTask(models.Model):
    """
    Class to store AmpliSeq analyses submitted to batch
    """
    ampliseq = models.ForeignKey(
        AmpliSeqRequest,
        on_delete=models.CASCADE,
        related_name='azuretask'
    )
    exit_code_file = models.CharField(max_length=2048)
