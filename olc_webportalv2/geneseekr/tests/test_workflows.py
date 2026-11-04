from unittest import mock

from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.geneseekr.models import (
    AMRSummary,
    GeneSeekrRequest,
    NearestNeighbors,
    ProkkaRequest,
    TopBlastHit,
    Tree,
)
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.users.models import User


class GeneSeekrWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='geneseekr-workflow-test',
            password='password',
        )
        SequenceData.objects.create(
            seqid='2015-SEQ-0711',
            quality='Pass',
            genus='Listeria',
        )
        SequenceData.objects.create(
            seqid='2015-SEQ-0712',
            quality='Pass',
            genus='Listeria',
        )
        self.client.login(
            username='geneseekr-workflow-test',
            password='password',
        )

    @mock.patch('olc_webportalv2.geneseekr.views.run_geneseekr')
    def test_query_creates_processing_request_and_queues_task(self, run_task):
        response = self.client.post(
            reverse('geneseekr:geneseekr_query'),
            {
                'seqids': '2015-SEQ-0711',
                'query_sequence': '>query\nACGT',
                'name': 'Workflow query',
            },
        )

        request = GeneSeekrRequest.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.status, 'Processing')
        self.assertEqual(request.query_sequence, '>query\nACGT')
        self.assertEqual(request.name, 'Workflow query')
        self.assertEqual(run_task.apply_async.call_count, 1)

    def test_processing_page_saves_new_email(self):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            query_sequence='>query\nACGT',
        )

        response = self.client.post(
            reverse(
                'geneseekr:geneseekr_processing',
                kwargs={'geneseekr_request_pk': request.pk},
            ),
            {'email': 'alerts@example.com'},
        )

        request.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.emails_array, ['alerts@example.com'])

    @mock.patch('olc_webportalv2.geneseekr.views.id_sync', return_value={})
    def test_results_page_calculates_gene_summary(self, id_sync):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            query_sequence='>query\nACGT',
            gene_targets=['geneA'],
        )
        TopBlastHit.objects.create(
            geneseekr_request=request,
            contig_name='contig-1',
            query_coverage=90.0,
            percent_identity=95.0,
            start_position=1,
            end_position=4,
            e_value=0.001,
            gene_name='geneA',
        )

        response = self.client.get(
            reverse(
                'geneseekr:geneseekr_results',
                kwargs={'geneseekr_request_pk': request.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['gene_summaries'], [{
            'name': 'geneA',
            'hit_count': 1,
            'average_identity': 95.0,
            'best_identity': 95.0,
            'average_coverage': 90.0,
        }])
        self.assertEqual(id_sync.call_count, 1)

    def test_submission_views_create_processing_requests(self):
        with mock.patch('olc_webportalv2.geneseekr.views.run_mash') as run_mash:
            response = self.client.post(
                reverse('geneseekr:tree_request'),
                {'seqids': '2015-SEQ-0711 2015-SEQ-0712'},
            )
        tree = Tree.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(tree.status, 'Processing')
        self.assertEqual(run_mash.apply_async.call_count, 1)

        with mock.patch(
            'olc_webportalv2.geneseekr.views.run_amr_summary'
        ) as run_amr_summary:
            response = self.client.post(
                reverse('geneseekr:amr_request'),
                {'seqids': '2015-SEQ-0711'},
            )
        amr_request = AMRSummary.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(amr_request.status, 'Processing')
        self.assertEqual(run_amr_summary.apply_async.call_count, 1)

        with mock.patch('olc_webportalv2.geneseekr.views.run_prokka') as run_prokka:
            response = self.client.post(
                reverse('geneseekr:prokka_request'),
                {'seqids': '2015-SEQ-0711'},
            )
        prokka_request = ProkkaRequest.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(prokka_request.status, 'Processing')
        self.assertEqual(run_prokka.apply_async.call_count, 1)

        with mock.patch(
            'olc_webportalv2.geneseekr.views.run_nearest_neighbors'
        ) as run_neighbors:
            response = self.client.post(
                reverse('geneseekr:neighbor_request'),
                {'seqid': '2015-SEQ-0711', 'number_neighbors': 10},
            )
        neighbor_request = NearestNeighbors.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(neighbor_request.status, 'Processing')
        self.assertEqual(run_neighbors.apply_async.call_count, 1)

    def test_other_users_cannot_view_results(self):
        other_user = User.objects.create_user(username='another-user')
        owned_requests = [
            (GeneSeekrRequest.objects.create(user=other_user), 'geneseekr:geneseekr_results', 'geneseekr_request_pk'),
            (Tree.objects.create(user=other_user), 'geneseekr:tree_result', 'tree_request_pk'),
            (AMRSummary.objects.create(user=other_user), 'geneseekr:amr_result', 'amr_request_pk'),
            (ProkkaRequest.objects.create(user=other_user), 'geneseekr:prokka_result', 'prokka_request_pk'),
            (NearestNeighbors.objects.create(user=other_user, number_neighbors=2), 'geneseekr:neighbor_result', 'neighbor_request_pk'),
        ]

        for request, url_name, parameter in owned_requests:
            response = self.client.get(
                reverse(url_name, kwargs={parameter: request.pk})
            )
            self.assertEqual(response.status_code, 404, url_name)

    def test_amr_and_prokka_email_submissions_are_saved(self):
        amr_request = AMRSummary.objects.create(user=self.user)
        response = self.client.post(
            reverse('geneseekr:amr_result', kwargs={'amr_request_pk': amr_request.pk}),
            {'email': 'amr@example.com'},
        )
        self.assertEqual(response.status_code, 200)
        amr_request.refresh_from_db()
        self.assertEqual(amr_request.emails_array, ['amr@example.com'])

        prokka_request = ProkkaRequest.objects.create(user=self.user)
        response = self.client.post(
            reverse('geneseekr:prokka_result', kwargs={'prokka_request_pk': prokka_request.pk}),
            {'email': 'prokka@example.com'},
        )
        self.assertEqual(response.status_code, 200)
        prokka_request.refresh_from_db()
        self.assertEqual(prokka_request.emails_array, ['prokka@example.com'])

    def test_duplicate_email_is_not_added_twice(self):
        request = GeneSeekrRequest.objects.create(
            user=self.user,
            emails_array=['alerts@example.com'],
        )

        response = self.client.post(
            reverse(
                'geneseekr:geneseekr_processing',
                kwargs={'geneseekr_request_pk': request.pk},
            ),
            {'email': 'alerts@example.com'},
        )

        request.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.emails_array, ['alerts@example.com'])

    def test_invalid_email_is_not_saved(self):
        request = GeneSeekrRequest.objects.create(user=self.user)

        response = self.client.post(
            reverse(
                'geneseekr:geneseekr_processing',
                kwargs={'geneseekr_request_pk': request.pk},
            ),
            {'email': 'not-an-email'},
        )

        request.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.emails_array, [])

    def test_home_delete_does_not_delete_another_users_request(self):
        other_user = User.objects.create_user(username='delete-target')
        request = GeneSeekrRequest.objects.create(user=other_user)

        response = self.client.post(
            reverse('geneseekr:geneseekr_home'),
            {'delete': request.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(GeneSeekrRequest.objects.filter(pk=request.pk).exists())