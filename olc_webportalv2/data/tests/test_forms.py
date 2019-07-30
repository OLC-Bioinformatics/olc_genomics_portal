from django.test import TestCase
from django.http import QueryDict
from olc_webportalv2.data.forms import DataRequestForm
from olc_webportalv2.metadata.models import SequenceData
from django.core.files.uploadedfile import SimpleUploadedFile


class DataFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0711',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0712',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()

    def test_valid_form(self):
        form = DataRequestForm({'seqids': '2015-SEQ-0711 2015-SEQ-0712'})
        self.assertTrue(form.is_valid())

    def test_invalid_form_wrong_regex(self):
        form = DataRequestForm({'seqids': '2015-SEQ-0711 2015-SEQ-07124'})
        self.assertFalse(form.is_valid())

    def test_invalid_form_seqid_does_not_exist(self):
        form = DataRequestForm({'seqids': '2015-SEQ-0711 2020-SEQ-0712'})
        self.assertFalse(form.is_valid())
