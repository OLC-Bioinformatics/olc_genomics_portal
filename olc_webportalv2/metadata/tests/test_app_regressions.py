"""Regression tests for metadata forms and REST endpoints."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class MetadataApiTests(TestCase):
    """Check error responses from metadata API detail endpoints."""

    def test_missing_oln_returns_not_found(self):
        """Return HTTP 404 rather than success-shaped JSON for unknown OLNs."""
        response = self.client.get(
            reverse('metadata:olndata_detail', kwargs={'oln_id': 'missing'})
        )

        self.assertEqual(response.status_code, 404)

    def test_missing_sequence_returns_not_found_for_admin(self):
        """Return HTTP 404 when an admin requests an unknown sequence."""
        admin = get_user_model().objects.create_user(
            username='metadata-admin',
            is_staff=True,
        )
        self.client.force_login(admin)

        response = self.client.get(
            reverse('metadata:sequencedata_detail', kwargs={'pk': 9999})
        )

        self.assertEqual(response.status_code, 404)
