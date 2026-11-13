"""Tests for sequence data request forms."""

from django.test import TestCase

from olc_webportalv2.data.forms import DataRequestForm
from olc_webportalv2.metadata.models import SequenceData


class DataFormTest(TestCase):
    """Test validation of requested sequence identifiers."""

    @classmethod
    def setUpTestData(cls):
        """Create sequence records used by form validation tests."""
        sequence_data = SequenceData.objects.create(
            seqid='2015-SEQ-0711',
            quality='Pass',
            genus='Listeria',
            subspecies='',
        )
        sequence_data.save()
        sequence_data = SequenceData.objects.create(
            seqid='2015-SEQ-0712',
            quality='Pass',
            genus='Listeria',
            subspecies='',
        )
        sequence_data.save()

    def test_valid_form(self):
        """Accept correctly formatted IDs present in the catalog."""
        form = DataRequestForm({'seqids': '2015-SEQ-0711 2015-SEQ-0712'})
        self.assertTrue(form.is_valid())

    def test_invalid_form_wrong_regex(self):
        """Reject IDs that do not match the SEQID format."""
        form = DataRequestForm({'seqids': '2015-SEQ-0711 2015-SEQ-07124'})
        self.assertFalse(form.is_valid())

    def test_invalid_form_seqid_does_not_exist(self):
        """Reject IDs absent from the metadata catalog."""
        form = DataRequestForm({'seqids': '2015-SEQ-0711 2020-SEQ-0712'})
        self.assertFalse(form.is_valid())
