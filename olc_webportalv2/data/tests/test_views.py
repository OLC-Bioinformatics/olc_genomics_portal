from django.test import TestCase
from django.urls import reverse
from olc_webportalv2.users.models import User
from olc_webportalv2.data.models import DataRequest


class DataTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username='TestUser')
        user.set_password('password')
        user.save()
        user1 = User.objects.create(username='Test')
        user1.set_password('password')
        user1.save()
        data_request = DataRequest.objects.create(user=user,
                                                  seqids=['2015-SEQ-0711'],
                                                  download_link='asdf',
                                                  request_type='Fasta')
        data_request.save()

    def test_data_home_login_required(self):
        resp = self.client.get(reverse('data:data_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_data_home(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('data:data_home'))
        self.assertEqual(resp.status_code, 200)

    def test_assembled_data_login_required(self):
        resp = self.client.get(reverse('data:assembled_data'))
        self.assertEqual(resp.status_code, 302)

    def test_assembled_data_get(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('data:assembled_data'))
        self.assertEqual(resp.status_code, 200)

    def test_data_download_login_required(self):
        resp = self.client.get(reverse('data:data_download', kwargs={'data_request_pk': 1}))
        self.assertEqual(resp.status_code, 302)

    def test_data_download(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('data:data_download', kwargs={'data_request_pk': 1}))
        self.assertEqual(resp.status_code, 200)

    def test_data_download_404(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('data:data_download', kwargs={'data_request_pk': 9999}))
        self.assertEqual(resp.status_code, 404)
