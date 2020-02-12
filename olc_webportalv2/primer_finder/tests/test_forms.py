from django.test import TestCase
from django.http import QueryDict
from olc_webportalv2.primer_finder.forms import PrimerForm
from django.core.files.uploadedfile import SimpleUploadedFile
from olc_webportalv2.users.models import User

class PrimerFinderFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username='TestProject')
        user.set_password('password')
        user.save()

    def test_invalid_form_EPCR(self):
        form = PrimerForm({
            'analysistype': 'custom',
            'primer_file': '',
            'mismatches': '0',
        })
        self.assertFalse(form.is_valid())

    def test_valid_form_EPCR(self):
        form = PrimerForm({
            'analysistype': 'custom',
            'ampliconsize': '1000',
            'mismatches': '0',
            'export_amplicons': 'True',
            'primer_file': 'test.fasta',
        })
        self.assertTrue(form.is_valid())
