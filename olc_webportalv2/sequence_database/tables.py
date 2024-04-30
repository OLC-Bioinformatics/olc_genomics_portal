import django_tables2 as tables
from olc_webportalv2.sequence_database.models import SequenceData


class SequenceDataTable(tables.Table):

    seqid = tables.Column(attrs={"td": {"nowrap": "nowrap"}},
                          order_by='seqid')
    cfiaid = tables.Column(attrs={"td": {"nowrap": "nowrap"}})

    class Meta:
        model = SequenceData
        # orderable = False
        attrs = {"class": "sequencetable",
                 "display": "inline-block",
                 "position": "relative",
                 "overflow": "auto",
                 "style": "overflow-y:scroll"}
        fields = ('seqid', 'cfiaid', 'rmlst', 'mlst', 'mlst_cc', 'genus', 'species', 'serovar', 'geneseekr', 'vtyper',
                  'version', 'typing_date')
