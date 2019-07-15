from rest_framework.test import APITestCase
from django.conf import settings
from olc_webportalv2.users.models import User
from olc_webportalv2.cowbat.models import SequencingRun
from azure.storage.blob import BlockBlobService
import time
import json


class TestAPI(APITestCase):

    @classmethod
    def setUpClass(cls):
        # setUpClass runs only once, instead of every test case.
        # This means we get to upload a bunch of files, and then check that they're really there.
        # These tests have to get run in top to bottom order, so they're not (strictly speaking) unit tests,
        # but they get run in order by default, so my worries are fairly minimal.
        super(TestAPI, cls).setUpClass()
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        # Make sure container doesn't already exist. API will autoconvert _ to - and make the whole thing lowercase
        blob_client.delete_container('111111-fake')
        # Give a bit of time for the delete operation to go through. Painfully long for a unit test, but sometimes
        # the delete operation takes quite a while. At least it only happens once!
        time.sleep(40)
        user = User.objects.create(username='TestUser')
        user.set_password('password')
        user.save()

    def test_upload_get_requires_auth(self):
        response = self.client.get('/api/upload/fake_run/fake_file')
        self.assertEqual(response.status_code, 403)  # Should be forbidden if not logged in

    def test_upload_put_requires_auth(self):
        response = self.client.put('/api/upload/fake_run/fake_file')
        self.assertEqual(response.status_code, 403)  # Should be forbidden if not logged in

    def test_file_does_not_exist(self):
        self.client.login(username='TestUser', password='password')
        response = self.client.get('/api/upload/fake_run/fake_file', format='json')
        response_dict = json.loads(response.content.decode('utf-8'))
        self.assertFalse(response_dict['exists'])
        self.assertEqual(response_dict['size'], 0)

    def test_file_upload_samplesheet(self):
        self.client.login(username='TestUser', password='password')
        with open('olc_webportalv2/test_files/SampleSheet.csv', 'rb') as infile:
            response = self.client.put('/api/upload/111111_FAKE/SampleSheet.csv', infile.read(), content_type='text/csv')
        sequencing_run = SequencingRun.objects.get(run_name='111111_FAKE')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_exists = blob_client.exists(container_name='111111-fake',
                                         blob_name='SampleSheet.csv')
        self.assertTrue(blob_exists)
        self.assertIn('2018-SEQ-1471', sequencing_run.seqids)
        self.assertEqual(len(sequencing_run.seqids), 24)
        self.assertEqual(response.status_code, 204)

    def test_file_upload_fastq(self):
        self.client.login(username='TestUser', password='password')
        with open('olc_webportalv2/test_files/2018-SEQ-1471_S1_L001_R1_001.fastq.gz', 'rb') as infile:
            response = self.client.put('/api/upload/111111_FAKE/2018-SEQ-1471_S1_L001_R1_001.fastq.gz',
                                       infile.read(), content_type='application/gzip')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_exists = blob_client.exists(container_name='111111-fake',
                                         blob_name='2018-SEQ-1471_S1_L001_R1_001.fastq.gz')
        self.assertTrue(blob_exists)
        self.assertEqual(response.status_code, 204)

    def test_file_upload_interop(self):
        self.client.login(username='TestUser', password='password')
        with open('olc_webportalv2/test_files/InterOp/QMetricsOut.bin', 'rb') as infile:
            response = self.client.put('/api/upload/111111_FAKE/QMetricsOut.bin',
                                       infile.read(),
                                       content_type='application/octet-stream')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_exists = blob_client.exists(container_name='111111-fake',
                                         blob_name='InterOp/QMetricsOut.bin')
        self.assertTrue(blob_exists)
        self.assertEqual(response.status_code, 204)

    def test_file_upload_metadata_file(self):
        self.client.login(username='TestUser', password='password')
        with open('olc_webportalv2/test_files/RunInfo.xml', 'rb') as infile:
            response = self.client.put('/api/upload/111111_FAKE/RunInfo.xml',
                                       infile.read(),
                                       content_type='text/xml')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_exists = blob_client.exists(container_name='111111-fake',
                                         blob_name='RunInfo.xml')
        self.assertTrue(blob_exists)
        self.assertEqual(response.status_code, 204)

    def test_get_interop_file_exists(self):
        self.client.login(username='TestUser', password='password')
        response = self.client.get('/api/upload/111111_FAKE/QMetricsOut.bin')
        response_dict = json.loads(response.content.decode('utf-8'))
        self.assertTrue(response_dict['exists'])
        self.assertGreater(response_dict['size'], 0)

    def test_get_interop_file_does_not_exist(self):
        self.client.login(username='TestUser', password='password')
        response = self.client.get('/api/upload/111111_FAKE/ControlMetricsOut.bin')
        response_dict = json.loads(response.content.decode('utf-8'))
        self.assertFalse(response_dict['exists'])
        self.assertEqual(response_dict['size'], 0)

    def test_get_metadata_file_exists(self):
        self.client.login(username='TestUser', password='password')
        response = self.client.get('/api/upload/111111_FAKE/RunInfo.xml')
        response_dict = json.loads(response.content.decode('utf-8'))
        self.assertTrue(response_dict['exists'])
        self.assertGreater(response_dict['size'], 0)

    def test_get_metadata_file_does_not_exist(self):
        self.client.login(username='TestUser', password='password')
        response = self.client.get('/api/upload/111111_FAKE/runParameters.xml')
        response_dict = json.loads(response.content.decode('utf-8'))
        self.assertFalse(response_dict['exists'])
        self.assertEqual(response_dict['size'], 0)

    def test_get_fastq_file_exists(self):
        self.client.login(username='TestUser', password='password')
        response = self.client.get('/api/upload/111111_FAKE/2018-SEQ-1471_S1_L001_R1_001.fastq.gz')
        response_dict = json.loads(response.content.decode('utf-8'))
        self.assertTrue(response_dict['exists'])
        self.assertGreater(response_dict['size'], 0)

    def test_get_fastq_file_does_not_exist(self):
        self.client.login(username='TestUser', password='password')
        response = self.client.get('/api/upload/111111_FAKE/2018-SEQ-1471_not_real_L001_R1_001.fastq.gz')
        response_dict = json.loads(response.content.decode('utf-8'))
        self.assertFalse(response_dict['exists'])
        self.assertEqual(response_dict['size'], 0)
