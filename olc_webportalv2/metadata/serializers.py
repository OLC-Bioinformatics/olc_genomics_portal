"""REST serializers for metadata and OLC network sequence results."""

from rest_framework import serializers

from olc_webportalv2.metadata.models import OLNID, SequenceData


class SequenceDataSerializer(serializers.HyperlinkedModelSerializer):
    """Serialize sequence metadata for the admin API."""

    class Meta:
        """Configure the sequence metadata fields."""

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
    """Represent a related sequence as its ID and quality."""

    def to_representation(self, value):
        """Return a compact sequence ID and quality string."""
        return f'{value.seqid}: {value.quality}'


class OLNSerializer(serializers.ModelSerializer):
    """Serialize an OLC network ID and its associated sequences."""

    seqids = SeqIDListingField(many=True, read_only=True)

    class Meta:
        """Configure the OLC network serializer fields."""

        model = OLNID
        fields = ('olnid', 'seqids')
