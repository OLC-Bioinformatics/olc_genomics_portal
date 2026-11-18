from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.geneseekr.models import GeneSeekrRequest
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.users.models import User


class GeneSeekrSubmissionIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='geneseekr-upload-integration',
            password='password',
        )
        SequenceData.objects.create(
            seqid='2015-SEQ-0711',
            quality='Pass',
            genus='Listeria',
            subspecies='',
        )
        self.client.force_login(self.user)

    @mock.patch('olc_webportalv2.geneseekr.views.run_geneseekr')
    def test_uploaded_fasta_is_saved_and_queued(self, run_task):
        query_sequence = '>query\nACGT\n'
        upload = SimpleUploadedFile(
            'query.fasta',
            query_sequence.encode('utf-8'),
            content_type='text/plain',
        )
        response = self.client.post(
            reverse('geneseekr:geneseekr_query'),
            {
                'seqids': '2015-SEQ-0711',
                'query_file': upload,
                'name': 'Uploaded query',
            },
        )

        request = GeneSeekrRequest.objects.get(user=self.user)
        self.assertRedirects(
            response,
            reverse(
                'geneseekr:geneseekr_processing',
                kwargs={'geneseekr_request_pk': request.pk},
            ),
        )
        self.assertEqual(request.status, 'Processing')
        self.assertEqual(request.query_sequence, query_sequence)
        self.assertEqual(request.name, 'Uploaded query')
        run_task.apply_async.assert_called_once_with(
            queue='cowbat',
            args=(request.pk,),
            time_limit=600,
        )
