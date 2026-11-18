from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.metadata.models import Genus, MetaDataRequest, SequenceData
from olc_webportalv2.users.models import User


class MetadataQueryIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='metadata-integration',
            password='password',
        )
        Genus.objects.create(genus='Escherichia')
        for seqid, quality in (
            ('1234-SEQ-0001', 'Pass'),
            ('1234-SEQ-0002', 'Reference'),
            ('1234-SEQ-0003', 'Fail'),
        ):
            SequenceData.objects.create(
                seqid=seqid,
                genus='Escherichia',
                quality=quality,
                subspecies='',
            )

    def setUp(self):
        self.client.force_login(self.user)

    def submit_search(self, quality):
        response = self.client.post(
            reverse('metadata:metadata_home'),
            {'genus': 'Escherichia', 'quality': quality},
        )

        self.assertEqual(response.status_code, 302)
        search = MetaDataRequest.objects.get()
        self.assertRedirects(
            response,
            reverse(
                'metadata:metadata_results',
                kwargs={'metadata_request_pk': search.pk},
            ),
        )
        return search

    def test_pass_quality_includes_pass_and_reference_sequences(self):
        search = self.submit_search('Pass')

        self.assertEqual(
            set(search.seqids),
            {'1234-SEQ-0001', '1234-SEQ-0002'},
        )

    def test_reference_quality_includes_only_reference_sequences(self):
        search = self.submit_search('Reference')

        self.assertEqual(search.seqids, ['1234-SEQ-0002'])

    def test_all_quality_includes_low_quality_sequences(self):
        search = self.submit_search('Fail')

        self.assertEqual(
            set(search.seqids),
            {'1234-SEQ-0001', '1234-SEQ-0002', '1234-SEQ-0003'},
        )
