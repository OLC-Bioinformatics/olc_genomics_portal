#!/usr/bin/env python

"""
Models for primer finder.
"""

# Django imports
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

# Portal-specific imports
from olc_webportalv2.users.models import User


class PrimerVerifierRequest(models.Model):
    """
    Model representing a primer verifier request.

    Args:
        models (Model): Django model class.
    """
    STATUS_CHOICES = [
        ('Unprocessed', 'Unprocessed'),
        ('Processing', 'Processing'),
        ('Complete', 'Complete'),
        ('Error', 'Error'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, db_index=True)
    project_name = models.CharField(max_length=256, blank=True)
    status = models.CharField(
        max_length=64,
        choices=STATUS_CHOICES,
        default='Unprocessed',
        db_index=True
    )
    report_download_link = models.CharField(max_length=256, blank=True)
    summary_download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    errors = models.TextField(blank=True, default=str())
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)
    emails_array = ArrayField(models.EmailField(
        max_length=100), blank=True, null=True, default=list)
    report = models.TextField(blank=True)
    summary = models.TextField(blank=True)

    min_amplicon_size = models.IntegerField(
        default=0,
        validators=[MaxValueValidator(10000), MinValueValidator(0)]
    )
    max_amplicon_size = models.IntegerField(
        default=1500,
        validators=[MaxValueValidator(10000), MinValueValidator(0)]
    )
    mismatches = models.IntegerField(
        default=2,
        validators=[MaxValueValidator(3), MinValueValidator(0)]
    )
    contig_breaks = models.BooleanField(default=False)
    range_buffer = models.IntegerField(
        default=0,
        validators=[MaxValueValidator(200), MinValueValidator(0)]
    )

    primer_sequences = models.TextField(blank=True)

    # Probe
    probe_sequence = models.TextField(blank=True)

    inclusivity_panel = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    exclusivity_panel = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )

    def __str__(self):
        return self.project_name

    def container_namer(self):
        """
        Returns the container name for the primer validator request.
        """
        return 'primer-validator-' + str(self.pk)


# Inclusivity/exclusivity panel choices
CAMPYLOBACTER = 'campylobacter'
ESCHERICHIA = 'escherichia'
LISTERIA = 'listeria'
BDS_SALMONELLA = 'bds-salmonella'
NCBI_SALMONELLA = 'ncbi-salmonella'
VTEC = 'vtec'
EXCLUSIVITY = 'bds-exclusivity'
STX_OPERONS = 'stx'


genera = [
    (CAMPYLOBACTER, "Campylobacter"),
    (ESCHERICHIA, "Escherichia"),
    (VTEC, "VTEC"),
    (LISTERIA, "Listeria"),
    (BDS_SALMONELLA, "BDS-Salmonella"),
    (NCBI_SALMONELLA, "NCBI-Salmonella"),
    (EXCLUSIVITY, "BDS-Exclusivity"),
    (STX_OPERONS, "STX-Operons"),
]


class VerifierPrimerSet(models.Model):
    """
    Model representing a set of primers within a primer verification request.
    """
    verifier_request = models.ForeignKey(
        PrimerVerifierRequest,
        on_delete=models.CASCADE,
        related_name='primer_id')
    primer_name = models.CharField(max_length=64, blank=False)

    def __str__(self):
        return 'primer-set-{primer_name}-{pk}'.format(
            primer_name=self.primer_name,
            pk=str(self.pk)
        )


class VerifierPrimers(models.Model):
    """
    Model representing a primer within a primer verification request.
    """
    primer = models.ForeignKey(
        VerifierPrimerSet,
        on_delete=models.CASCADE,
        related_name='primer')
    primer_header = models.CharField(max_length=64, blank=False)
    primer_sequence = models.CharField(max_length=64, blank=False)

    def __str__(self):
        return 'primer-{header}-{pk}'.format(header=self.primer_header,
                                             pk=str(self.pk))

    class Meta:
        """
        Naming options for the VerifierPrimers model.
        """
        verbose_name = _('verifier primers')
        verbose_name_plural = _('verifier primers')


class VerifierPanel(models.Model):
    """
    Model representing a panel within a primer verification request.
    """
    verifier_request = models.ForeignKey(
        PrimerVerifierRequest,
        on_delete=models.CASCADE,
        related_name='panel_details')
    genus = models.CharField(max_length=64, blank=False)
    panel = models.CharField(max_length=64, blank=False)

    def __str__(self):
        return '{panel_type}-panel-{genus}-{pk}'\
            .format(panel_type=self.panel,
                    genus=self.genus,
                    pk=str(self.pk))


class VerifierSEQID(models.Model):
    """
    Model representing a sequence ID within a primer verification request.

    Args:
        panel (VerifierPanel): The panel associated with this sequence ID.
    """
    panel = models.ForeignKey(
        VerifierPanel,
        on_delete=models.CASCADE,
        related_name='sequence')
    primer = models.ForeignKey(
        VerifierPrimerSet,
        null=True,
        on_delete=models.CASCADE,
        related_name='seqid_primer_set'
    )
    seqid = models.CharField(max_length=64, blank=False)
    sequence_path = models.CharField(max_length=64, blank=False)
    amplicon_length = models.CharField(
        max_length=64, blank=True, default=str()
    )
    contig = models.CharField(max_length=64, blank=True, default=str())
    direction = models.CharField(max_length=64, blank=True, default=str())
    forward_mismatch = models.CharField(
        max_length=64, blank=True, default=str()
    )
    forward_mismatch_details = models.CharField(
        max_length=64, blank=True, default=str()
    )
    forward_pos = models.CharField(max_length=64, blank=True, default=str())
    forward_query = models.CharField(max_length=64, blank=True, default=str())
    forward_ref = models.CharField(max_length=64, blank=True, default=str())
    primer_set = models.CharField(max_length=64, blank=True, default=str())
    reverse_mismatch = models.CharField(
        max_length=64, blank=True, default=str()
    )
    reverse_mismatch_details = models.CharField(
        max_length=64, blank=True, default=str()
    )
    reverse_pos = models.CharField(max_length=64, blank=True, default=str())
    reverse_query = models.CharField(max_length=64, blank=True, default=str())
    reverse_ref = models.CharField(max_length=64, blank=True, default=str())
    sequence = models.TextField(default=str())
    start_pos = models.CharField(max_length=64, blank=True, default=str())
    stop_pos = models.CharField(max_length=64, blank=True, default=str())
    total_mismatch = models.CharField(max_length=64, blank=True, default=str())

    def __str__(self):
        return 'sequence-details-{seqid}-{primer}-{panel}-{pk}'\
            .format(seqid=self.seqid,
                    panel=self.panel,
                    primer=self.primer,
                    pk=str(self.pk))

    class Meta:
        """
        Naming options for the VerifierSEQID model.
        """
        verbose_name = _('verifier sequence details')
        verbose_name_plural = _('verifier sequence details')


class VerifierAzureRequest(models.Model):
    """
    Model representing a request to the Azure service for primer verification.

    Args:
        verifier_request (PrimerVerifierRequest): The primer verification
        request associated with this Azure request.
    """
    verifier_request = models.ForeignKey(
        PrimerVerifierRequest,
        on_delete=models.CASCADE, related_name='azuretask'
    )
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)

    def __str__(self):
        return 'verifier-azure-request-{pk}'.format(pk=self.pk)


class ValidatorRequest(models.Model):
    # Metadata information
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project_name = models.CharField(max_length=256, blank=True)
    status = models.CharField(max_length=64, default='Unprocessed')
    report_download_link = models.CharField(max_length=256, blank=True)
    summary_download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    errors = models.TextField(blank=True, default=str())
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)
    emails_array = ArrayField(
        models.EmailField(max_length=100), blank=True, null=True, default=list
    )
    # JSON report from PrimerValidator
    report = models.TextField(blank=True)
    # JSON summary after calculating panel-specific totals
    summary = models.TextField(blank=True)
    # JSON summary of number of mismatches-specific details
    totals = models.TextField(blank=True)

    # primer_validator.py arguments
    mismatches = models.IntegerField(
        default=2,
        validators=[MaxValueValidator(3), MinValueValidator(0)]
    )

    # Primers
    forward_primer = models.TextField(blank=False)
    reverse_primer = models.TextField(blank=False)

    # Probe
    probe_sequence = models.TextField(blank=True)

    # Sequences
    inclusivity_panel = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )
    exclusivity_panel = ArrayField(
        models.CharField(max_length=24), blank=True, default=list
    )

    def __str__(self):
        return self.project_name

    def container_namer(self):
        container_name = 'primer-verifier-' + str(self.pk)
        return container_name


class ValidatorPrimerSet(models.Model):
    validator_request = models.ForeignKey(
        ValidatorRequest,
        on_delete=models.CASCADE,
        related_name='primer_id')
    primer_name = models.CharField(max_length=64, blank=False)

    def __str__(self):
        return 'primer-set-{primer_name}-{pk}'.format(
            primer_name=self.primer_name,
            pk=str(self.pk)
        )


class ValidatorPrimers(models.Model):
    """
    Model representing a primer within a primer set.
    """
    primer = models.ForeignKey(
        ValidatorPrimerSet,
        on_delete=models.CASCADE,
        related_name='primer')
    primer_header = models.CharField(max_length=64, blank=False)
    primer_sequence = models.CharField(max_length=64, blank=False)

    def __str__(self):
        return 'primer-{header}-{pk}'.format(header=self.primer_header,
                                             pk=str(self.pk))

    class Meta:
        """
        Naming conventions for validator primers.
        """
        verbose_name = _('validator primers')
        verbose_name_plural = _('validator primers')


class ValidatorPanel(models.Model):
    """
    Model representing a panel within a primer verification request.
    """
    validator_request = models.ForeignKey(
        ValidatorRequest,
        on_delete=models.CASCADE,
        related_name='panel_details')
    genus = models.CharField(max_length=64, blank=False)
    panel = models.CharField(max_length=64, blank=False)

    def __str__(self):
        return '{panel_type}-panel-{genus}-{pk}' \
            .format(panel_type=self.panel,
                    genus=self.genus,
                    pk=str(self.pk))


class ValidatorSEQID(models.Model):
    """
    Model representing a sequence ID within a panel.
    """
    panel = models.ForeignKey(
        ValidatorPanel,
        on_delete=models.CASCADE,
        related_name='sequence')
    primer = models.ForeignKey(
        ValidatorPrimerSet,
        null=True,
        on_delete=models.CASCADE,
        related_name='seqid_primer_set'
    )
    seqid = models.CharField(max_length=64, blank=False)
    sequence_path = models.CharField(max_length=64, blank=False)
    amplicon_length = models.CharField(
        max_length=64, blank=True, default=str()
    )
    contig = models.CharField(max_length=64, blank=True, default=str())
    direction = models.CharField(max_length=64, blank=True, default=str())
    forward_mismatch = models.CharField(
        max_length=64, blank=True, default=str()
    )
    forward_mismatch_details = models.CharField(
        max_length=64, blank=True, default=str()
    )
    forward_pos = models.CharField(
        max_length=64, blank=True, default=str()
    )
    forward_query = models.CharField(
        max_length=64, blank=True, default=str()
    )
    forward_ref = models.CharField(
        max_length=64, blank=True, default=str()
    )
    primer_set = models.CharField(
        max_length=64, blank=True, default=str()
    )
    reverse_mismatch = models.CharField(
        max_length=64, blank=True, default=str()
    )
    reverse_mismatch_details = models.CharField(
        max_length=64, blank=True, default=str()
    )
    reverse_pos = models.CharField(
        max_length=64, blank=True, default=str()
    )
    reverse_query = models.CharField(
        max_length=64, blank=True, default=str()
    )
    reverse_ref = models.CharField(
        max_length=64, blank=True, default=str()
    )
    sequence = models.TextField(default=str())
    start_pos = models.CharField(
        max_length=64, blank=True, default=str()
    )
    stop_pos = models.CharField(
        max_length=64, blank=True, default=str()
    )
    total_mismatch = models.CharField(
        max_length=64, blank=True, default=str()
    )

    def __str__(self):
        return 'sequence-details-{seqid}-{panel}-{pk}'\
            .format(seqid=self.seqid,
                    panel=self.panel,
                    pk=str(self.pk))

    class Meta:
        """
        Naming options for the validator sequence details.
        """
        verbose_name = _('validator sequence details')
        verbose_name_plural = _('validator sequence details')


class PrimerValidatorAzureRequest(models.Model):
    """
    Model representing a request to the Azure validation service.
    """
    validator_request = models.ForeignKey(
        ValidatorRequest, on_delete=models.CASCADE, related_name='azuretask'
    )
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)

    def __str__(self):
        return 'validator-azure-request-{pk}'.format(pk=self.pk)


class PrimerFinderRequest(models.Model):
    """
    Model representing a request for inclusivity and exclusivity sequence IDs.
    """
    inclusivity_seqids = models.CharField(max_length=64, blank=True)
    exclusivity_seqids = models.CharField(max_length=64, blank=True)


class InclusivitySequences(models.Model):
    """
    Model representing inclusivity sequences.
    """
    inclusivity_seqids = models.ForeignKey(
        PrimerFinderRequest,
        on_delete=models.CASCADE,
        related_name='inclusivity_sequences')
    inclusivity_seqid = models.CharField(
        max_length=24,
        blank=False)


class ExclusivitySequences(models.Model):
    """
    Model representing exclusivity sequences.
    """
    exclusivity_seqids = models.ForeignKey(
        PrimerFinderRequest,
        on_delete=models.CASCADE,
        related_name='exclusivity_sequences')
    exclusivity_seqid = models.CharField(
        max_length=24,
        blank=False)
