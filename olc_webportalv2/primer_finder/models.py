#!/usr/bin/env python

# Django imports
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.translation import ugettext_lazy as _
from django.contrib.postgres.fields import ArrayField
from django.db import models

# Portal-specific imports
from olc_webportalv2.users.models import User


class PrimerVerifierRequest(models.Model):
    # Metadata information
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project_name = models.CharField(max_length=256, blank=True)
    status = models.CharField(max_length=64, default='Unprocessed')
    report_download_link = models.CharField(max_length=256, blank=True)
    summary_download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    errors = models.TextField(blank=True, default=str())
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)
    emails_array = ArrayField(models.EmailField(max_length=100), blank=True, null=True, default=list)
    # JSON report from PrimerValidator
    report = models.TextField(blank=True)
    # JSON summary after calculating genus-specific totals
    summary = models.TextField(blank=True)

    # Size variables
    minimum = 0
    range_maximum = 200
    maximum = 10000
    # primer_validator.py arguments
    min_amplicon_size = models.IntegerField(default=0, validators=[MaxValueValidator(10000), MinValueValidator(0)])
    max_amplicon_size = models.IntegerField(default=1500, validators=[MaxValueValidator(10000), MinValueValidator(0)])
    mismatches = models.IntegerField(default=2, validators=[MaxValueValidator(3), MinValueValidator(0)])
    contig_breaks = models.BooleanField(default=False)
    range_buffer = models.IntegerField(default=0, validators=[MaxValueValidator(200), MinValueValidator(0)])

    # Primers
    primer_sequences = models.TextField(blank=True)
    # Sequences
    inclusivity_panel = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    exclusivity_panel = ArrayField(models.CharField(max_length=24), blank=True, default=list)

    def __str__(self):
        return self.project_name

    def container_namer(self):
        container_name = 'primer-verifier-' + str(self.pk)
        return container_name


# Inclusivity/exclusivity panel choices
campylobacter = 'campylobacter'
eschericia = 'escherichia'
listeria = 'listeria'
salmonella = 'salmonella'
vtec = 'vtec'

genera = [
    (campylobacter, 'Campylobacter'),
    (eschericia, 'Escherichia'),
    (listeria, 'Listeria'),
    (salmonella, 'Salmonella'),
    (vtec, 'VTEC')
]


class VerifierPrimerSet(models.Model):
    verifier_request = models.ForeignKey(
        PrimerVerifierRequest,
        on_delete=models.CASCADE,
        related_name='primer_id')
    primer_name = models.CharField(max_length=64, blank=False)

    def __str__(self):
        return 'primer-set-{primer_name}-{pk}'.format(primer_name=self.primer_name,
                                                      pk=str(self.pk))


class VerifierPrimers(models.Model):
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
        verbose_name = _('verifier primers')
        verbose_name_plural = _('verifier primers')


class VerifierPanel(models.Model):
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
    amplicon_length = models.CharField(max_length=64, blank=True, default=str())
    contig = models.CharField(max_length=64, blank=True, default=str())
    direction = models.CharField(max_length=64, blank=True, default=str())
    forward_mismatch = models.CharField(max_length=64, blank=True, default=str())
    forward_mismatch_details = models.CharField(max_length=64, blank=True, default=str())
    forward_pos = models.CharField(max_length=64, blank=True, default=str())
    forward_query = models.CharField(max_length=64, blank=True, default=str())
    forward_ref = models.CharField(max_length=64, blank=True, default=str())
    primer_set = models.CharField(max_length=64, blank=True, default=str())
    reverse_mismatch = models.CharField(max_length=64, blank=True, default=str())
    reverse_mismatch_details = models.CharField(max_length=64, blank=True, default=str())
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
        verbose_name = _('verifier sequence details')
        verbose_name_plural = _('verifier sequence details')


class VerifierAzureRequest(models.Model):
    verifier_request = models.ForeignKey(PrimerVerifierRequest, on_delete=models.CASCADE, related_name='azuretask')
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
    emails_array = ArrayField(models.EmailField(max_length=100), blank=True, null=True, default=list)
    # JSON report from PrimerValidator
    report = models.TextField(blank=True)
    # JSON summary after calculating panel-specific totals
    summary = models.TextField(blank=True)
    # JSON summary of number of mismatches-specific details
    totals = models.TextField(blank=True)

    # primer_validator.py arguments
    mismatches = models.IntegerField(default=2, validators=[MaxValueValidator(3), MinValueValidator(0)])

    # Primers
    forward_primer = models.TextField(blank=False)
    reverse_primer = models.TextField(blank=False)

    # Probe
    probe_sequence = models.TextField(blank=True)

    # Sequences
    inclusivity_panel = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    exclusivity_panel = ArrayField(models.CharField(max_length=24), blank=True, default=list)

    def __str__(self):
        return self.project_name

    def container_namer(self):
        container_name = 'primer-validator-' + str(self.pk)
        return container_name


class ValidatorPrimerSet(models.Model):
    validator_request = models.ForeignKey(
        ValidatorRequest,
        on_delete=models.CASCADE,
        related_name='primer_id')
    primer_name = models.CharField(max_length=64, blank=False)

    def __str__(self):
        return 'primer-set-{primer_name}-{pk}'.format(primer_name=self.primer_name,
                                                      pk=str(self.pk))


class ValidatorPrimers(models.Model):
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
        verbose_name = _('validator primers')
        verbose_name_plural = _('validator primers')


class ValidatorPanel(models.Model):
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
    amplicon_length = models.CharField(max_length=64, blank=True, default=str())
    contig = models.CharField(max_length=64, blank=True, default=str())
    direction = models.CharField(max_length=64, blank=True, default=str())
    forward_mismatch = models.CharField(max_length=64, blank=True, default=str())
    forward_mismatch_details = models.CharField(max_length=64, blank=True, default=str())
    forward_pos = models.CharField(max_length=64, blank=True, default=str())
    forward_query = models.CharField(max_length=64, blank=True, default=str())
    forward_ref = models.CharField(max_length=64, blank=True, default=str())
    primer_set = models.CharField(max_length=64, blank=True, default=str())
    reverse_mismatch = models.CharField(max_length=64, blank=True, default=str())
    reverse_mismatch_details = models.CharField(max_length=64, blank=True, default=str())
    reverse_pos = models.CharField(max_length=64, blank=True, default=str())
    reverse_query = models.CharField(max_length=64, blank=True, default=str())
    reverse_ref = models.CharField(max_length=64, blank=True, default=str())
    sequence = models.TextField(default=str())
    start_pos = models.CharField(max_length=64, blank=True, default=str())
    stop_pos = models.CharField(max_length=64, blank=True, default=str())
    total_mismatch = models.CharField(max_length=64, blank=True, default=str())

    def __str__(self):
        return 'sequence-details-{seqid}-{panel}-{pk}'\
            .format(seqid=self.seqid,
                    panel=self.panel,
                    pk=str(self.pk))

    class Meta:
        verbose_name = _('validator sequence details')
        verbose_name_plural = _('validator sequence details')


class PrimerValidatorAzureRequest(models.Model):
    validator_request = models.ForeignKey(ValidatorRequest, on_delete=models.CASCADE, related_name='azuretask')
    exit_code_file = models.CharField(max_length=256, blank=True, null=True)

    def __str__(self):
        return 'validator-azure-request-{pk}'.format(pk=self.pk)


class PrimerFinderRequest(models.Model):
    inclusivity_seqids = models.CharField(max_length=64, blank=True)
    exclusivity_seqids = models.CharField(max_length=64, blank=True)


class InclusivitySequences(models.Model):
    inclusivity_seqids = models.ForeignKey(
        PrimerFinderRequest,
        on_delete=models.CASCADE,
        related_name='inclusivity_sequences')
    inclusivity_seqid = models.CharField(
        max_length=24,
        blank=False)


class ExclusivitySequences(models.Model):
    exclusivity_seqids = models.ForeignKey(
        PrimerFinderRequest,
        on_delete=models.CASCADE,
        related_name='exclusivity_sequences')
    exclusivity_seqid = models.CharField(
        max_length=24,
        blank=False)
