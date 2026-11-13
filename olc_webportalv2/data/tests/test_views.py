"""Tests for sequence data request views."""

from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.data.models import DataRequest
from olc_webportalv2.users.models import User


class DataTestCase(TestCase):
    """Test authentication and ownership rules for data request views."""

    @classmethod
    def setUpTestData(cls):
        """Create two users and one request owned by the first user."""
        user = User.objects.create(username='TestUser')
        user.set_password('password')
        user.save()
        user1 = User.objects.create(username='Test')
        user1.set_password('password')
        user1.save()
        cls.data_request = DataRequest.objects.create(
            user=user,
            seqids=['2015-SEQ-0711'],
            download_link='asdf',
            request_type='Fasta',
        )

    def test_data_home_login_required(self):
        """Redirect anonymous users to the login page."""
        resp = self.client.get(reverse('data:data_home'))
        self.assertEqual(
            resp.status_code, 302
        )  # Should get 302 redirected if user is not logged in.

    def test_data_home(self):
        """Allow an authenticated user to view their request list."""
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('data:data_home'))
        self.assertEqual(resp.status_code, 200)

    def test_assembled_data_login_required(self):
        """Require authentication before showing the FASTA request form."""
        resp = self.client.get(reverse('data:assembled_data'))
        self.assertEqual(resp.status_code, 302)

    def test_assembled_data_get(self):
        """Render the FASTA request form for an authenticated user."""
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('data:assembled_data'))
        self.assertEqual(resp.status_code, 200)

    def test_data_download_login_required(self):
        """Require authentication before showing a request result."""
        resp = self.client.get(
            reverse('data:data_download', kwargs={'data_request_pk': 1})
        )
        self.assertEqual(resp.status_code, 302)

    def test_data_download(self):
        """Allow the owner to view their request result."""
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(
            reverse(
                'data:data_download',
                kwargs={'data_request_pk': self.data_request.pk},
            )
        )
        self.assertEqual(resp.status_code, 200)

    def test_data_download_is_restricted_to_the_request_owner(self):
        """Prevent another user from viewing this request."""
        self.client.login(username='Test', password='password')
        response = self.client.get(
            reverse(
                'data:data_download',
                kwargs={'data_request_pk': self.data_request.pk},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_data_download_404(self):
        """Return 404 when the requested record does not exist."""
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(
            reverse(
                'data:data_download',
                kwargs={'data_request_pk': self.data_request.pk + 100000},
            )
        )
        self.assertEqual(resp.status_code, 404)
