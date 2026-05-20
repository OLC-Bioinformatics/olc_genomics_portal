from django.test import TestCase
from olc_webportalv2.cowbat.models import SequencingRun


class SampleTestCase(TestCase):
    def setUp(cls):
        run_one = SequencingRun.objects.create(run_name='123456_TEST')

    def test_correct_str_method(self):
        self.assertEqual(str(SequencingRun.objects.get(run_name='123456_TEST')), '123456_TEST')
