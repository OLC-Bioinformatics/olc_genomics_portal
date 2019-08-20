from django.contrib.postgres.fields import ArrayField
from olc_webportalv2.users.models import User
from django.db import models


class VirTyperProject(models.Model):
    # request = models.ForeignKey(VirTyperRequest, on_delete=models.CASCADE, related_name='vir_typer_request')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project_name = models.CharField(max_length=256, unique=True, error_messages={'unique': 'Project name must be unique!'})
    status = models.CharField(max_length=64, default='Unprocessed')
    # emails_array = ArrayField(models.EmailField(max_length=100), blank=True, null=True, default=[])
    download_link = models.CharField(max_length=256, blank=True)
    created_at = models.DateField(auto_now_add=True)
    report = models.CharField(max_length=100000, blank=True)
    # The cleaned consensus sequence
    # processed_sequence = models.CharField(max_length=256, blank=True)
    #
    # fasta_header = models.CharField(max_length=64, blank=False)
    # predicted_organism = ''
    # comments = ''

    def __str__(self):
        return self.project_name

    def container_namer(self):
        container_name = 'vir-typer-' + str(self.pk) + '-' + str(self.project_name).lower().replace('_', '-')
        return container_name


class VirTyperRequest(models.Model):
    STHY = 'STH'
    BURNABY = 'BUR'
    LABS = [
        (STHY, 'Ste-Hyacinthe'),
        (BURNABY, 'Burnaby'),
    ]

    NORI = 'NOVI'
    NORII = 'NOVII'
    HAV = 'HAV'
    VIRUSES = [
        (NORI, 'Norovirus genogroup 1'),
        (NORII, 'Norovirus genogroup 2'),
        (HAV, 'Hepatitus A')
    ]

    project_name = models.ForeignKey(VirTyperProject, on_delete=models.CASCADE, related_name='project_request')
    lab_ID = models.CharField(max_length=20, choices=LABS, default=STHY, blank=False)
    isolate_source = models.CharField(max_length=50, blank=False)
    LSTS_ID = models.CharField(max_length=50, blank=False)
    putative_classification = models.CharField(max_length=50, choices=VIRUSES, default=NORI, blank=False)
    sample_name = models.CharField(max_length=50, blank=False)
    subunit = models.CharField(max_length=3, blank=False)
    date_received = models.DateTimeField(blank=False)
    analyst_name = models.CharField(max_length=50, blank=False)

    def __str__(self):
        return self.sample_name


class VirTyperFiles(models.Model):
    sample_name = models.ForeignKey(VirTyperRequest, on_delete=models.CASCADE, related_name='sample_files')
    sequence_file = models.CharField(max_length=256, blank=False)

    def __str__(self):
        return self.sequence_file


class VirTyperResults(models.Model):
    sequence_file = models.ForeignKey(VirTyperFiles, on_delete=models.CASCADE, related_name='file_results')
    allele = models.CharField(max_length=50, blank=False)
    orientation = models.CharField(max_length=50, blank=False)
    forward_primer = models.CharField(max_length=50, blank=False)
    reverse_primer = models.CharField(max_length=50, blank=False)
    trimmed_sequence = models.CharField(max_length=256, blank=False)
    trimmed_sequence_len = models.CharField(max_length=50, blank=False)
    trimmed_quality_max = models.CharField(max_length=50, blank=False)
    trimmed_quality_mean = models.CharField(max_length=50, blank=False)
    trimmed_quality_min = models.CharField(max_length=50, blank=False)
    trimmed_quality_stdev = models.CharField(max_length=50, blank=False)

# class VirTyperBlobContainerName(models.Model):
#     project_name = models.ForeignKey(VirTyperProject, on_delete=models.CASCADE, related_name='project_blob_container')
#     container_name = models.CharField(max_length=64, blank=False)

# class VirTyperReport(models.Model):
#     request = models.ForeignKey(VirTyperRequest, on_delete=models.CASCADE, related_name='vir_typer_request')
#     sample = models.ForeignKey(VirTyperSample, on_delete=models.CASCADE, related_name='vir_typer_sample')


class VirTyperAzureRequest(models.Model):
    project_name = models.ForeignKey(VirTyperProject, on_delete=models.CASCADE, related_name='project_azure_request')
    exit_code_file = models.CharField(max_length=256, blank=False)
