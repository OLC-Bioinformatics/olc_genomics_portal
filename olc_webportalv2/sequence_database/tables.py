#!/usr/bin/env python3
from olc_webportalv2.sequence_database.models import SequenceData, UniqueGenus
import django_tables2 as tables
__author__ = 'adamkoziol'


class SequenceDataTable(tables.Table):
    class Meta:
        model = SequenceData
        # template_name = "django_tables2/bootstrap-responsive.html"
        fields = ['genus', 'species', 'mlst', 'mlstcc', 'rmlst', 'geneseekr', 'serovar', 'vtyper', 'version', 'typing_date']
        attrs = {"class": "dbtable"}

