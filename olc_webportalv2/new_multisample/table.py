import django_tables2 as tables

from olc_webportalv2.new_multisample.models import SendsketchResult


class SendsketchTable(tables.Table):
    class Meta:
        model = SendsketchResult
        attrs = {'class': 'paleblue'}
