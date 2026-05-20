from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.utils.translation import ugettext_lazy as _

# verbose names are needed for translation!


class LabID(models.Model):
    labid = models.CharField(max_length=24,)

    def __str__(self):
        return self.labid


class OLNID(models.Model):
    olnid = models.CharField(max_length=24)

    def __str__(self):
        return self.olnid


class Genus(models.Model):
    seqid = models.CharField(max_length=24)
    genus = models.CharField(max_length=48, blank=True, verbose_name=_("genus"),)

    def __str__(self):
        return self.genus


class Species(models.Model):
    seqid = models.CharField(max_length=24)
    species = models.CharField(max_length=48, blank=True, verbose_name=_("species"),)

    def __str__(self):
        return self.species


class MLST(models.Model):
    seqid = models.CharField(max_length=24)
    mlst = models.CharField(max_length=12, blank=True)
    version = models.CharField(max_length=48, verbose_name=_('pipeline_version'), blank=True, null=True)
    typing_date = models.DateField(blank=True, null=True, verbose_name=_('typing_date'))

    def __str__(self):
        return self.mlst


class MLSTCC(models.Model):
    seqid = models.CharField(max_length=24)
    mlst_cc = models.CharField(max_length=12, blank=True)
    version = models.CharField(max_length=48, verbose_name=_('pipeline_version'), blank=True, null=True)
    typing_date = models.DateField(blank=True, null=True, verbose_name=_('typing_date'))

    def __str__(self):
        return self.mlst_cc


class RMLST(models.Model):
    seqid = models.CharField(max_length=24)
    rmlst = models.CharField(max_length=12, blank=True)
    version = models.CharField(max_length=48, verbose_name=_('pipeline_version'), blank=True, null=True)
    typing_date = models.DateField(blank=True, null=True, verbose_name=_('typing_date'))

    def __str__(self):
        return self.rmlst


class GeneSeekr(models.Model):
    seqid = models.CharField(max_length=24)
    geneseekr = models.CharField(max_length=48, blank=True)
    version = models.CharField(max_length=48, verbose_name=_('pipeline_version'), blank=True, null=True)
    typing_date = models.DateField(blank=True, null=True, verbose_name=_('typing_date'))

    def __str__(self):
        return self.geneseekr


class Vtyper(models.Model):
    seqid = models.CharField(max_length=24)
    vtyper = models.CharField(max_length=48, blank=True)
    version = models.CharField(max_length=48, verbose_name=_('pipeline_version'), blank=True, null=True)
    typing_date = models.DateField(blank=True, null=True, verbose_name=_('typing_date'))

    def __str__(self):
        return self.vtyper


class Serovar(models.Model):
    seqid = models.CharField(max_length=24)
    serovar = models.CharField(max_length=512, blank=True, verbose_name=_("serovar"),)
    version = models.CharField(max_length=48, verbose_name=_('pipeline_version'), blank=True, null=True)
    typing_date = models.DateField(blank=True, null=True, verbose_name=_('typing_date'))

    def __str__(self):
        return self.serovar


class NameTable(models.Model):
    seqid = models.CharField(max_length=24)
    cfiaid = models.CharField(max_length=48)
    curatorflag = models.CharField(max_length=48)

    def __str__(self):
        return self.cfiaid


class LookupTable(models.Model):
    cfiaid = models.ForeignKey(NameTable, on_delete=models.CASCADE)
    seqid = models.CharField(max_length=24)
    olnid = models.CharField(max_length=48)
    labid = models.CharField(max_length=48)
    biosample = models.CharField(max_length=48)
    other_names = ArrayField(models.CharField(max_length=512), null=True, blank=True, default=list)

    def __str__(self):
        return self.cfiaid.cfiaid


class UniqueGenus(models.Model):
    genus = models.CharField(max_length=24)

    def __str__(self):
        return self.genus


class UniqueSpecies(models.Model):
    species = models.CharField(max_length=24)

    def __str__(self):
        return self.species


class UniqueMLST(models.Model):
    mlst = models.CharField(max_length=24)

    def __str__(self):
        return self.mlst


class UniqueMLSTCC(models.Model):
    mlst_cc = models.CharField(max_length=24)

    def __str__(self):
        return self.mlst_cc


class UniqueRMLST(models.Model):
    rmlst = models.CharField(max_length=24)

    def __str__(self):
        return self.rmlst


class UniqueGeneSeekr(models.Model):
    geneseekr = models.CharField(max_length=48)

    def __str__(self):
        return self.geneseekr


class UniqueSerovar(models.Model):
    serovar = models.CharField(max_length=512)

    def __str__(self):
        return self.serovar


class UniqueVtyper(models.Model):
    vtyper = models.CharField(max_length=24)

    def __str__(self):
        return self.vtyper


class SequenceData(models.Model):
    seqid = models.CharField(max_length=24, verbose_name=_('SEQID'))
    cfiaid = models.ForeignKey(NameTable, on_delete=models.CASCADE, verbose_name=_('CFIA ID'))
    # rMLST is numeric, but categorical, so keep as CharField
    rmlst = models.ForeignKey(RMLST, on_delete=models.CASCADE, null=True, verbose_name=_('rMLST Profile'))
    # Same as rMLST. Numeric, but categorical.
    mlst = models.ForeignKey(MLST, on_delete=models.CASCADE, null=True, verbose_name=_('MLST Profile'))
    mlst_cc = models.ForeignKey(MLSTCC, on_delete=models.CASCADE, null=True, verbose_name=_('MLST Clonal Complex'))
    genus = models.ForeignKey(Genus, on_delete=models.CASCADE, null=True, verbose_name=_('Genus'))
    species = models.ForeignKey(Species, on_delete=models.CASCADE, null=True, verbose_name=_('Species'))
    serovar = models.ForeignKey(Serovar, on_delete=models.CASCADE, null=True, verbose_name=_('Serovar'))
    geneseekr = models.ForeignKey(GeneSeekr, on_delete=models.CASCADE, null=True, verbose_name=_('GeneSeekr Profile'))
    vtyper = models.ForeignKey(Vtyper, on_delete=models.CASCADE, null=True, verbose_name=_('Vtyper Profile'))
    version = models.CharField(max_length=48, verbose_name=_('Database Version'), blank=True, null=True)
    typing_date = models.DateField(blank=True, null=True, verbose_name=_('Typing Date'))

    def __str__(self):
        return self.seqid

    def print_species(self):
        return self.species.species


class DatabaseRequestIDs(models.Model):
    seqids = ArrayField(models.CharField(max_length=24), blank=True, default=list)
    cfiaids = ArrayField(models.CharField(max_length=48), blank=True, default=list)


class DatabaseRequest(models.Model):
    seqid = models.CharField(max_length=24)
    cfiaid = models.CharField(max_length=48)
    rmlst = models.CharField(max_length=48)
    mlst = models.CharField(max_length=24)
    mlst_cc = models.CharField(max_length=24)
    genus = models.CharField(max_length=48)
    species = models.CharField(max_length=48)
    serovar = models.CharField(max_length=512)
    geneseekr = models.CharField(max_length=48)
    vtyper = models.CharField(max_length=48)
    version = models.CharField(max_length=48, verbose_name=_('pipeline_version'), blank=True, null=True)
    typing_date = models.DateField(blank=True, null=True, verbose_name=_('typing_date'))


class DatabaseQuery(models.Model):

    SEQID = 'SEQID'
    CFIAID = 'CFIAID'
    GENUS = 'GENUS'
    SPECIES = 'SPECIES'
    MLST = 'MLST'
    MLSTCC = 'MLSTCC'
    RMLST = 'RMLST'
    GENESEEKR = 'GENESEEKR'
    SEROVAR = 'SEROVAR'
    VTYPER = 'VTYPER'
    VERSION = 'VERSION'

    FIELDS = [
        (GENUS, _('Genus')),
        (SPECIES, _('Species')),
        (MLST, _('MLST Profile')),
        (MLSTCC, _('MLST Clonal Complex')),
        (RMLST, _('rMLST Profile')),
        (GENESEEKR, _('GeneSeekr Profile')),
        (SEROVAR, _('Serovar')),
        (VTYPER, _('Vtyper Profile')),
        (VERSION, _('Pipeline Version'))
    ]

    AND = 'AND'
    OR = 'OR'
    NOT = 'NOT'

    OPERATORS = [
        (AND, _('and')),
        (OR, _('or')),
        (NOT, _('not'))
    ]

    CONTAINS = 'CONTAINS'
    EXACT = 'EXACT'

    QUALIFIERS = [
        (CONTAINS, _('contains')),
        (EXACT, _('exact')),
    ]
    database_fields = models.CharField(max_length=24, choices=FIELDS, default=GENUS, blank=False)
    query_operators = models.CharField(max_length=12, choices=OPERATORS, default=AND, blank=False)
    qualifiers = models.CharField(max_length=12, choices=QUALIFIERS, default=CONTAINS, blank=False)
    query = models.CharField(max_length=48)
