from django.test import TestCase, Client
from django.urls import reverse
from olc_webportalv2.users.models import User
from olc_webportalv2.cowbat.models import SequencingRun

class SampleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username='TestUser')
        user.set_password('password')
        user.save()
        run_one = SequencingRun.objects.create(run_name='181213_TEST')
        run_two = SequencingRun.objects.create(run_name='123456_TEST')
    # Views to test here: cowbat_processing, assembly_home, upload_metadata, upload_interop, upload_sequence_data, retry_sequence_data_upload

    def test_assembly_home_login_required(self):
        resp = self.client.get(reverse('cowbat:assembly_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_assembly_home(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('cowbat:assembly_home'))
        self.assertEqual(resp.status_code, 200)
        sequencing_runs = SequencingRun.objects.filter()
        for run in sequencing_runs:
            self.assertIn(run.run_name, resp.content.decode('utf-8'))

    def test_cowbat_processing_login_required(self):
        resp = self.client.get(reverse('cowbat:cowbat_processing', kwargs={'sequencing_run_pk': 1}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_cowbat_processing_404_no_run(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('cowbat:cowbat_processing', kwargs={'sequencing_run_pk': 123}))
        self.assertEqual(resp.status_code, 404)


