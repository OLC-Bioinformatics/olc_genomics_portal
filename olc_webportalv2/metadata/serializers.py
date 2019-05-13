from rest_framework import serializers

from olc_webportalv2.metadata.models import SequenceData


class SequenceDataSerializer(serializers.HyperlinkedModelSerializer):

    class Meta:
        model = SequenceData
        fields = ('id', 'seqid', 'quality', 'genus', 'species', 'serotype', 'mlst', 'rmlst')
