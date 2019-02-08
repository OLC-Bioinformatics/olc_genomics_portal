from django.test import TestCase
from olc_webportalv2.cowbat.models import SequencingRun


class SampleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        run_one = SequencingRun.objects.create(run_name='123456_TEST')

    def test_correct_str_method(self):
        self.assertEqual(str(SequencingRun.objects.get(pk=1)), '123456_TEST')
