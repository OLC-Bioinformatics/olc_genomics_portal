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
        run_one = SequencingRun.objects.create(run_name='181213_TEST',
                                               seqids=['2222-SEQ-0001', '2222-SEQ-0002'],
                                               realtime_strains={'2222-SEQ-0001': 'False',
                                                                 '2222-SEQ-0002': 'True'})
        run_two = SequencingRun.objects.create(run_name='123456_TEST')

    # Views to test here: cowbat_processing, assembly_home, upload_metadata, upload_interop, upload_sequence_data, retry_sequence_data_upload
    def test_assembly_home_login_required(self):
        resp = self.client.get(reverse('cowbat:assembly_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    #Verifying that all the run names are in the table 
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

    def test_realtime_validate_login_required(self):
        resp = self.client.get(reverse('cowbat:verify_realtime', kwargs={'sequencing_run_pk': 1}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_realtime_validate_no_strains_selected(self):
        data = {'realtime_select': []}
        self.client.login(username='TestUser', password='password')
        resp = self.client.post('cowbat:verify_realtime', data, kwargs={'sequencing_run_pk': 1})
        self.assertEqual(resp.status_code, 200)  # Make sure page actually loads.
        run_one = SequencingRun.objects.get(run_name='181213_TEST')
        self.assertEqual(run_one.realtime_strains['2222-SEQ-0001'], 'False')
        self.assertEqual(run_one.realtime_strains['2222-SEQ-0002'], 'False')

    def test_realtime_validate_all_strains_selected(self):
        data = {'realtime_select': ['2222-SEQ-0001', '2222-SEQ-0002']}
        self.client.login(username='TestUser', password='password')
        resp = self.client.post('cowbat:verify_realtime', data, kwargs={'sequencing_run_pk': 1})
        self.assertEqual(resp.status_code, 200)  # Make sure page actually loads.
        run_one = SequencingRun.objects.get(run_name='181213_TEST')
        self.assertEqual(run_one.realtime_strains['2222-SEQ-0001'], 'True')
        self.assertEqual(run_one.realtime_strains['2222-SEQ-0002'], 'True')

    def test_realtime_validate_one_strain_selected(self):
        data = {'realtime_select': ['2222-SEQ-0001']}
        self.client.login(username='TestUser', password='password')
        resp = self.client.post('cowbat:verify_realtime', data, kwargs={'sequencing_run_pk': 1})
        self.assertEqual(resp.status_code, 200)  # Make sure page actually loads.
        run_one = SequencingRun.objects.get(run_name='181213_TEST')
        self.assertEqual(run_one.realtime_strains['2222-SEQ-0001'], 'True')
        self.assertEqual(run_one.realtime_strains['2222-SEQ-0002'], 'False')
