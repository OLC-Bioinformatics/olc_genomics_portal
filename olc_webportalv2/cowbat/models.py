"""Database models for COWBAT and Nanopore sequencing workflows."""

import os
from decimal import Decimal

from django.conf import settings  # To access azure credentials
from django.contrib.postgres.fields import ArrayField
from django.core.files.storage import FileSystemStorage
from django.db import models

# Create your models here.
# TODO: InterOp file doesn't (I don't think) get used at all any more.
# Actually delete it once verified that deleting it doesn't break everything.


class OverwriteStorage(FileSystemStorage):
    """Replace an existing uploaded file when a name is reused."""

    def get_available_name(self, name, max_length=None):
        """Return the requested name after removing any existing file.

        Args:
            name: Requested storage path.
            max_length: Maximum permitted path length, if configured.

        """
        if self.exists(name):
            os.remove(os.path.join(settings.MEDIA_ROOT, name))
        return name


def get_run_name(instance, filename):
    """Build the media path for a file belonging to a sequencing run.

    Args:
        instance: File model instance with an associated sequencing run.
        filename: Uploaded file name.

    """
    return os.path.join(instance.sequencing_run.run_name, filename)


def get_interop_name(instance, filename):
    """Build the InterOp media path for a sequencing run file.

    Args:
        instance: InterOp file instance with an associated sequencing run.
        filename: Uploaded file name.

    """
    return os.path.join(instance.sequencing_run.run_name, "InterOp", filename)


class SequencingRun(models.Model):
    """Track sequencing run metadata, workflow state, and uploaded files."""

    WORKFLOW_STATE_CHOICES = (
        ("created", "Created"),
        ("uploading", "Uploading"),
        ("upload_complete", "Upload complete"),
        ("submitting", "Submitting"),
        ("submitted", "Submitted"),
        ("processing", "Processing"),
        ("stopping", "Stopping"),
        ("ready", "Ready"),
        ("complete", "Complete"),
        ("error", "Error"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="sequencing_runs",
    )
    run_name = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=64, default="Unprocessed")
    progress = models.CharField(max_length=128, default="Unprocessed")
    errors = ArrayField(
        models.CharField(max_length=256), blank=True, default=list
    )
    exit_code = models.PositiveIntegerField(null=True, blank=True)
    seqids = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    forward_reads_to_upload = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    reverse_reads_to_upload = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    uploaded_forward_reads = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    uploaded_reverse_reads = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    sample_plate = models.JSONField(default=dict, blank=True, null=True)
    realtime_strains = models.JSONField(default=dict, blank=True, null=True)
    download_link = models.CharField(max_length=256, blank=True, default="")
    emails_array = ArrayField(
        models.EmailField(max_length=100), blank=True, default=list
    )
    container = models.CharField(max_length=64, blank=True, default="")
    workflow_state = models.CharField(
        max_length=32,
        choices=WORKFLOW_STATE_CHOICES,
        default="created",
        db_index=True,
    )
    failure_stage = models.CharField(max_length=64, blank=True, default="")
    task_finished_at = models.DateTimeField(blank=True, null=True)
    task_queue = models.CharField(max_length=64, blank=True, default="")
    task_retry_count = models.PositiveIntegerField(default=0)
    task_started_at = models.DateTimeField(blank=True, null=True)
    task_submitted_at = models.DateTimeField(blank=True, null=True)
    rename_status = models.CharField(max_length=16, blank=True, default="")
    rename_target = models.CharField(max_length=64, blank=True, default="")
    pool_id = models.CharField(max_length=64, blank=True, default="")
    job_id = models.CharField(max_length=64, blank=True, default="")
    task_id = models.CharField(max_length=64, blank=True, default="")
    batch_submit_status = models.CharField(
        max_length=64, blank=True, default=""
    )
    batch_submit_errors = models.CharField(
        max_length=64, blank=True, default=""
    )
    percent_complete = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.01")
    )
    # Arguments
    basic_assembly = models.BooleanField(default=False)
    preprocess = models.BooleanField(default=False)
    nextseq = models.BooleanField(default=False, null=True)

    def __str__(self):
        """Return the run name for display."""
        return self.run_name


class DataFile(models.Model):
    """Store an uploaded sequencing run metadata file."""

    sequencing_run = models.ForeignKey(
        SequencingRun, on_delete=models.CASCADE, related_name="datafile"
    )
    data_file = models.FileField(
        upload_to=get_run_name, storage=OverwriteStorage()
    )


class InterOpFile(models.Model):
    """Store a sequencing run's InterOp data file."""

    sequencing_run = models.ForeignKey(
        SequencingRun, on_delete=models.CASCADE, related_name="interop"
    )
    interop_file = models.FileField(upload_to=get_interop_name)


class AzureTask(models.Model):
    """Store the local exit-code file path for a Batch task."""

    sequencing_run = models.ForeignKey(
        SequencingRun, on_delete=models.CASCADE, related_name="azuretask"
    )
    exit_code_file = models.CharField(max_length=256)


class NanoporeFile(models.Model):
    """Track a POD5 upload and its processing assignment."""

    STATUS_CHOICES = (
        ("prepared", "Prepared"),
        ("uploading", "Uploading"),
        ("uploaded", "Uploaded"),
        ("error", "Error"),
    )
    sequencing_run = models.ForeignKey(
        SequencingRun, on_delete=models.CASCADE, related_name="nanopore_files"
    )
    relative_path = models.CharField(max_length=512)
    blob_name = models.CharField(max_length=512)
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="prepared"
    )
    attempt_count = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    processing_batch = models.ForeignKey(
        "NanoporeProcessingBatch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="files",
    )

    class Meta:
        """Enforce unique file paths within each sequencing run."""

        unique_together = (("sequencing_run", "relative_path"),)


class NanoporeRun(models.Model):
    """Store Nanopore run configuration and lifecycle timestamps."""

    STATUS_CHOICES = (
        ("created", "Created"),
        ("uploading", "Uploading"),
        ("ready", "Ready"),
        ("processing", "Processing"),
        ("stopping", "Stopping"),
        ("complete", "Complete"),
        ("error", "Error"),
    )
    sequencing_run = models.OneToOneField(
        SequencingRun, on_delete=models.CASCADE, related_name="nanopore_run"
    )
    instrument_name = models.CharField(max_length=128, blank=True, default="")
    instrument_type = models.CharField(max_length=128, blank=True, default="")
    source_directory = models.CharField(max_length=512, blank=True, default="")
    dorado_model = models.CharField(max_length=128, blank=True, default="")
    reference_panel = models.CharField(max_length=128, blank=True, default="")
    barcode_kit = models.CharField(max_length=64, blank=True, default="")
    barcode_values = models.JSONField(default=list, blank=True)
    sample_metadata = models.JSONField(default=dict, blank=True)
    scheduler_input_csv = models.TextField(blank=True, default="")
    scheduler_metadata_csv = models.TextField(blank=True, default="")
    scheduler_input_sha256 = models.CharField(
        max_length=64, blank=True, default=""
    )
    scheduler_metadata_sha256 = models.CharField(
        max_length=64, blank=True, default=""
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="created", db_index=True
    )
    started_at = models.DateTimeField(null=True, blank=True)
    last_file_seen_at = models.DateTimeField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    termination_requested_at = models.DateTimeField(null=True, blank=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    run_mode = models.CharField(max_length=16, default="streaming")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_nanopore_runs",
    )


class NanoporeManifest(models.Model):
    """Record the immutable file set assigned to a processing generation."""

    sequencing_run = models.ForeignKey(
        SequencingRun,
        on_delete=models.CASCADE,
        related_name="nanopore_manifests",
    )
    files = models.JSONField()
    sha256 = models.CharField(max_length=64)
    generation = models.PositiveIntegerField(default=1)
    finalized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class NanoporeProcessingBatch(models.Model):
    """Track submission and completion state for one Nanopore generation."""

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("submitting", "Submitting"),
        ("submitted", "Submitted"),
        ("running", "Running"),
        ("published", "Published"),
        ("complete", "Complete"),
        ("failed", "Failed"),
    )
    sequencing_run = models.ForeignKey(
        SequencingRun,
        on_delete=models.CASCADE,
        related_name="nanopore_processing_batches",
    )
    manifest = models.ForeignKey(
        NanoporeManifest,
        on_delete=models.CASCADE,
        related_name="processing_batches",
    )
    generation = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    azure_job_id = models.CharField(max_length=128, blank=True, default="")
    azure_task_id = models.CharField(max_length=128, blank=True, default="")
    azure_pool_id = models.CharField(max_length=128, blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    report = models.JSONField(default=dict, blank=True)
    report_url = models.CharField(max_length=1024, blank=True, default="")
    processed_pod5_count = models.PositiveIntegerField(default=0)
    pod5_count = models.PositiveIntegerField(default=0)
    last_completed_at = models.DateTimeField(null=True, blank=True)


class ResearchRun(models.Model):
    """Represent a research run stored in object storage."""

    run_name = models.CharField(max_length=64, unique=True)


class SummaryMetadata(models.Model):
    """Store parsed summary report data for a sequencing run."""

    sequencing_run = models.ForeignKey(
        SequencingRun, on_delete=models.CASCADE, related_name="summarymetadata"
    )
    summary_results = models.JSONField(default=dict, blank=True, null=True)
