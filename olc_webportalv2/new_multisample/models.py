from django.db import models
from olc_webportalv2.users.models import User
from django.contrib.postgres.fields.jsonb import JSONField
import os
from django.core.exceptions import ValidationError

# Create your models here.


def validate_fastq(fieldfile):
    filename = os.path.basename(fieldfile.name)
    if filename.endswith('.fastq.gz') or filename.endswith('.fastq'):
        print('File extension for {} confirmed valid'.format(filename))
    else:
        raise ValidationError(
            _('%(file)s does not end with .fastq or .fastq.gz'),
            params={'filename': filename},
        )


class ProjectMulti(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project_title = models.CharField(max_length=256)
    description = models.CharField(max_length=200, blank=True)
    date = models.DateTimeField(auto_now_add=True)

    forward_id = models.CharField(max_length=256, default='_R1')
    reverse_id = models.CharField(max_length=256, default='_R2')

    def __str__(self):
        return self.project_title


class Sample(models.Model):
    project = models.ForeignKey(ProjectMulti, on_delete=models.CASCADE, related_name='samples')
    file_R1 = models.FileField(upload_to='%Y%m%d%s', blank=True)
    file_R2 = models.FileField(upload_to='%Y%m%d%s', blank=True)
    file_fasta = models.FileField(upload_to='%Y%m%d%s', blank=True)
    title = models.CharField(max_length=200, blank=True)

    genesippr_status = models.CharField(max_length=128,
                                        default="Unprocessed")
    sendsketch_status = models.CharField(max_length=128,
                                         default="Unprocessed")
    confindr_status = models.CharField(max_length=128,
                                       default="Unprocessed")
    genomeqaml_status = models.CharField(max_length=128,
                                         default="Unprocessed")
    amr_status = models.CharField(max_length=128,
                                  default="Unprocessed")

    def __str__(self):
        return self.title


class GenomeQamlResult(models.Model):
    class Meta:
        verbose_name_plural = "GenomeQAML Results"
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='genomeqaml_result')
    predicted_class = models.CharField(max_length=128, default='N/A')
    percent_fail = models.CharField(max_length=128, default='N/A')
    percent_pass = models.CharField(max_length=128, default='N/A')
    percent_reference = models.CharField(max_length=118, default='N/A')

    def __str__(self):
        return '{}'.format(self.sample)


class SendsketchResult(models.Model):
    class Meta:
        verbose_name_plural = "Sendsketch Results"

    def __str__(self):
        return 'pk {}: Rank {}: Sample {}'.format(self.pk, self.rank, self.sample.pk)

    sample = models.ForeignKey(Sample, on_delete=models.CASCADE)
    rank = models.CharField(max_length=8, default='N/A')
    wkid = models.CharField(max_length=256, default='N/A')
    kid = models.CharField(max_length=256, default='N/A')
    ani = models.CharField(max_length=256, default='N/A')
    complt = models.CharField(max_length=256, default='N/A')
    contam = models.CharField(max_length=256, default='N/A')
    matches = models.CharField(max_length=256, default='N/A')
    unique = models.CharField(max_length=256, default='N/A')
    nohit = models.CharField(max_length=256, default='N/A')
    taxid = models.CharField(max_length=256, default='N/A')
    gsize = models.CharField(max_length=256, default='N/A')
    gseqs = models.CharField(max_length=256, default='N/A')
    taxname = models.CharField(max_length=256, default='N/A')


class GenesipprResults(models.Model):
    # For admin panel
    def __str__(self):
        return '{}'.format(self.sample)

    # TODO: Accomodate seqID
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='genesippr_results')

    # genesippr.csv
    strain = models.CharField(max_length=256, default="N/A")
    genus = models.CharField(max_length=256, default="N/A")

    # STEC
    serotype = models.CharField(max_length=256, default="N/A")
    o26 = models.CharField(max_length=256, default="N/A")
    o45 = models.CharField(max_length=256, default="N/A")
    o103 = models.CharField(max_length=256, default="N/A")
    o111 = models.CharField(max_length=256, default="N/A")
    o121 = models.CharField(max_length=256, default="N/A")
    o145 = models.CharField(max_length=256, default="N/A")
    o157 = models.CharField(max_length=256, default="N/A")
    uida = models.CharField(max_length=256, default="N/A")
    eae = models.CharField(max_length=256, default="N/A")
    eae_1 = models.CharField(max_length=256, default="N/A")
    vt1 = models.CharField(max_length=256, default="N/A")
    vt2 = models.CharField(max_length=256, default="N/A")
    vt2f = models.CharField(max_length=256, default="N/A")

    # listeria
    igs = models.CharField(max_length=256, default="N/A")
    hlya = models.CharField(max_length=256, default="N/A")
    inlj = models.CharField(max_length=256, default="N/A")

    # salmonella
    inva = models.CharField(max_length=256, default="N/A")
    stn = models.CharField(max_length=256, default="N/A")

    def inva_number(self):
        return float(self.inva.split('%')[0])

    def uida_number(self):
        return float(self.uida.split('%')[0])

    def vt1_number(self):
        return float(self.vt1.split('%')[0])

    def vt2_number(self):
        return float(self.vt2.split('%')[0])

    def vt2f_number(self):
        return float(self.vt2f.split('%')[0])

    def eae_number(self):
        return float(self.eae.split('%')[0])

    def eae_1_number(self):
        return float(self.eae_1.split('%')[0])

    def hlya_number(self):
        return float(self.hlya.split('%')[0])

    def igs_number(self):
        return float(self.igs.split('%')[0])

    def inlj_number(self):
        return float(self.inlj.split('%')[0])

    class Meta:
        verbose_name_plural = "Genesippr Results"


class GenesipprResultsSixteens(models.Model):
    class Meta:
        verbose_name_plural = "SixteenS Results"

    def __str__(self):
        return '{}'.format(self.sample)

    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='sixteens_results')

    # sixteens_full.csv
    strain = models.CharField(max_length=256, default="N/A")
    gene = models.CharField(max_length=256, default="N/A")
    percentidentity = models.CharField(max_length=256, default="N/A")
    genus = models.CharField(max_length=256, default="N/A")
    foldcoverage = models.CharField(max_length=256, default="N/A")

    @property
    def gi_accession(self):
        # Split by | delimiter, pull second element which should be the GI#
        gi_accession = self.gene.split('|')[1]
        return gi_accession


class GenesipprResultsGDCS(models.Model):
    class Meta:
        verbose_name_plural = "GDCS Results"

    def __str__(self):
        return '{}'.format(self.sample)

    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='gdcs_results')

    # GDCS.csv
    strain = models.CharField(max_length=256, default="N/A")
    genus = models.CharField(max_length=256, default="N/A")
    matches = models.CharField(max_length=256, default="N/A")
    meancoverage = models.CharField(max_length=128, default="N/A")
    passfail = models.CharField(max_length=16, default="N/A")
    allele_dict = JSONField(blank=True, null=True, default=dict)


class ConFindrResults(models.Model):
    class Meta:
        verbose_name_plural = 'Confindr Results'

    def __str__(self):
        return '{}'.format(self.sample)

    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='confindr_results')
    strain = models.CharField(max_length=256, default="N/A")
    genera_present = models.CharField(max_length=256, default="N/A")
    contam_snvs = models.CharField(max_length=256, default="N/A")
    contaminated = models.CharField(max_length=256, default="N/A")


class GenesipprResultsSerosippr(models.Model):
    class Meta:
        verbose_name_plural = "Serosippr Results"

    def __str__(self):
        return '{}'.format(self.sample)

    sample = models.ForeignKey(Sample, on_delete=models.CASCADE)


class AMRResult(models.Model):
    class Meta:
        verbose_name_plural = 'AMR Results'

    def __str__(self):
        return '{}'.format(self.sample)

    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='amr_results')
    results_dict = JSONField(blank=True, null=True, default=dict)
    species = models.CharField(max_length=88, default='N/A')
