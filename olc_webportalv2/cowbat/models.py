from django.db import models
from django.contrib.postgres.fields import ArrayField, JSONField
import os

from django.forms.widgets import EmailInput

# Create your models here.
# TODO: InterOp file doesn't (I don't think) get used at all any more.
# Actually delete it once verified that deleting it doesn't break everything.


def get_run_name(instance, filename):
    return os.path.join(instance.sequencing_run.run_name, filename)


def get_interop_name(instance, filename):
    return os.path.join(instance.sequencing_run.run_name, 'InterOp', filename)


class SequencingRun(models.Model):
    run_name = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=64, default='Unprocessed')
    progress = models.CharField(max_length=64, default='Unprocessed')
    errors = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    exit_code = models.PositiveIntegerField(null=True, blank=True)
    seqids = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    forward_reads_to_upload = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    reverse_reads_to_upload = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    uploaded_forward_reads = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    uploaded_reverse_reads = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    sample_plate = JSONField(default=dict, blank=True, null=True)
    realtime_strains = JSONField(default=dict, blank=True, null=True)
    download_link = models.CharField(max_length=256, blank=True, default='')
    emails_array = ArrayField(models.EmailField(max_length=100), blank=True, default=list)

    def __str__(self):
        return self.run_name


class DataFile(models.Model):
    sequencing_run = models.ForeignKey(SequencingRun, on_delete=models.CASCADE, related_name='datafile')
    data_file = models.FileField(upload_to=get_run_name)


class InterOpFile(models.Model):
    sequencing_run = models.ForeignKey(SequencingRun, on_delete=models.CASCADE, related_name='interop')
    interop_file = models.FileField(upload_to=get_interop_name)


class AzureTask(models.Model):
    sequencing_run = models.ForeignKey(SequencingRun, on_delete=models.CASCADE, related_name='azuretask')
    exit_code_file = models.CharField(max_length=256)
