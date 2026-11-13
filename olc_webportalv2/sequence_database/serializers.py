"""REST serializers for Sequence Database models."""

from rest_framework import serializers

from olc_webportalv2.sequence_database.models import OLNID, SequenceData


class SequenceDataSerializer(serializers.HyperlinkedModelSerializer):
    """Serialize a sequence and its associated metadata."""

    class Meta:
        model = SequenceData
        fields = (
            'id',
            'seqid',
            'quality',
            'genus',
            'species',
            'serotype',
            'mlst',
            'rmlst',
        )


class SeqIDListingField(serializers.RelatedField):
    """Render a sequence identifier and its quality label."""

    def to_representation(self, value):
        """Return the related sequence's display string.

        Args:
            value: The related sequence metadata object.
        """
        return f'{value.seqid}: {value.quality}'


class OLNSerializer(serializers.ModelSerializer):
    """Serialize an OLC network identifier and related sequences."""

    seqids = SeqIDListingField(many=True, read_only=True)

    class Meta:
        model = OLNID
        fields = ('olnid', 'seqids')
