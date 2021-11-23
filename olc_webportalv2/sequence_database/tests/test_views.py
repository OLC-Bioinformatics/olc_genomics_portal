from django.test import TestCase, Client
from django.urls import reverse
from olc_webportalv2.users.models import User
from olc_webportalv2.metadata.models import SequenceData, MetaDataRequest


class MetadataTest(TestCase):
    def setUp(self):
        user = User.objects.create(username='TestUser')
        user.set_password('password')
        user.save()
        SequenceData.objects.create(seqid='1234-SEQ-0001', genus='Escherichia', quality='Pass')
        SequenceData.objects.create(seqid='1234-SEQ-0002', genus='Escherichia', quality='Reference')
        SequenceData.objects.create(seqid='1234-SEQ-0003', genus='Escherichia', quality='Fail')
        SequenceData.objects.create(seqid='1234-SEQ-0004', genus='Listeria', quality='Pass')
        SequenceData.objects.create(seqid='1234-SEQ-0005', genus='Listeria', quality='Reference')
        SequenceData.objects.create(seqid='1234-SEQ-0006', genus='Listeria', quality='Fail')

    def test_metadata_home_page_login_required(self):
        resp = self.client.get(reverse('metadata:metadata_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def tearDown(self):
        pass
