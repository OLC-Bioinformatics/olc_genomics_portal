"""Tests for metadata page authentication and rendering."""

from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.users.models import User


class MetadataTest(TestCase):
    """Test access to the metadata search page."""

    def setUp(self):
        """Create a user and representative sequence records."""
        user = User.objects.create(username='TestUser')
        user.set_password('password')
        user.save()
        SequenceData.objects.create(
            seqid='1234-SEQ-0001',
            genus='Escherichia',
            quality='Pass',
            subspecies='',
        )
        SequenceData.objects.create(
            seqid='1234-SEQ-0002',
            genus='Escherichia',
            quality='Reference',
            subspecies='',
        )
        SequenceData.objects.create(
            seqid='1234-SEQ-0003',
            genus='Escherichia',
            quality='Fail',
            subspecies='',
        )
        SequenceData.objects.create(
            seqid='1234-SEQ-0004',
            genus='Listeria',
            quality='Pass',
            subspecies='',
        )
        SequenceData.objects.create(
            seqid='1234-SEQ-0005',
            genus='Listeria',
            quality='Reference',
            subspecies='',
        )
        SequenceData.objects.create(
            seqid='1234-SEQ-0006',
            genus='Listeria',
            quality='Fail',
            subspecies='',
        )

    def test_metadata_home_page_login_required(self):
        """Redirect anonymous users to the metadata search login."""
        resp = self.client.get(reverse('metadata:metadata_home'))
        self.assertEqual(
            resp.status_code, 302
        )  # Should get 302 redirected if user is not logged in.
