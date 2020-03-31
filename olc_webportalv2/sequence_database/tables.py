import django_tables2 as tables
from olc_webportalv2.sequence_database.models import SequenceData

class SequenceDataTable(tables.Table):
    class Meta:
        model = SequenceData
        attrs = {"class": "table"}