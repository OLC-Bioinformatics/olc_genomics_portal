from django.test import TestCase, Client
from django.urls import reverse
from olc_webportalv2.users.models import User
from olc_webportalv2.geneseekr.models import GeneSeekrRequest, Tree, AMRSummary, ProkkaRequest

class SampleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username='TestUser')
        user.set_password('password')
        user.save()
        user1 = User.objects.create(username='Test')
        user1.set_password('password')
        user1.save()
        request_one = GeneSeekrRequest.objects.create(name='123',query_sequence='>seqAAAAAAAA', user=user)
        request_two = GeneSeekrRequest.objects.create(name='456',query_sequence='>seqAAATTTTA', user=user)
        tree_one = Tree.objects.create(name='123',user=user)
        tree_two = Tree.objects.create(name='456',user=user)
        amr_one = AMRSummary.objects.create(name='123',user=user)
        amr_two = AMRSummary.objects.create(name='456',user=user)
        prokka_one = ProkkaRequest.objects.create(name='123',user=user)
        prokka_two = ProkkaRequest.objects.create(name='456',user=user)

# Geneseekr View Tests ----------------------------------------------------------------------------------------------------------------->

    def test_geneseekr_home_login_required(self):
        resp = self.client.get(reverse('geneseekr:geneseekr_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_geneseekr_home(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('geneseekr:geneseekr_home'))
        self.assertEqual(resp.status_code, 200)
        geneseekr_requests = GeneSeekrRequest.objects.filter()
        for request in geneseekr_requests:
            self.assertIn(request.name, resp.content.decode('utf-8'))
    
    def test_user_geneseekr_home(self):
        self.client.login(username='Test', password='password')
        resp = self.client.get(reverse('geneseekr:geneseekr_home'))
        self.assertEqual(resp.status_code, 200)
        geneseekr_requests = GeneSeekrRequest.objects.filter()
        for request in geneseekr_requests:
            self.assertNotIn(request.name, resp.content.decode('utf-8'))

    def test_geneseekr_processing_login_required(self):
        resp = self.client.get(reverse('geneseekr:geneseekr_processing', kwargs={'geneseekr_request_pk': 1}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_geneseekr_processing_404_no_run(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('geneseekr:geneseekr_processing', kwargs={'geneseekr_request_pk': 123}))
        self.assertEqual(resp.status_code, 404)

# Tree View Tests ----------------------------------------------------------------------------------------------------------------->

    def test_tree_home_login_required(self):
        resp = self.client.get(reverse('geneseekr:tree_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_tree_home(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('geneseekr:tree_home'))
        self.assertEqual(resp.status_code, 200)
        tree_requests = Tree.objects.filter()
        for request in tree_requests:
            self.assertIn(request.name, resp.content.decode('utf-8'))
    
    def test_user_tree_home(self):
        self.client.login(username='Test', password='password')
        resp = self.client.get(reverse('geneseekr:tree_home'))
        self.assertEqual(resp.status_code, 200)
        tree_requests = Tree.objects.filter()
        for request in tree_requests:
            self.assertNotIn(request.name, resp.content.decode('utf-8'))

    def test_tree_result_login_required(self):
        resp = self.client.get(reverse('geneseekr:tree_result', kwargs={'tree_request_pk': 1}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_tree_result_404_no_run(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('geneseekr:tree_result', kwargs={'tree_request_pk': 123}))
        self.assertEqual(resp.status_code, 404)

# AMR View Tests ----------------------------------------------------------------------------------------------------------------->

    def test_amr_home_login_required(self):
        resp = self.client.get(reverse('geneseekr:amr_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_amr_home(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('geneseekr:amr_home'))
        self.assertEqual(resp.status_code, 200)
        amr_requests = AMRSummary.objects.filter()
        for request in amr_requests:
            self.assertIn(request.name, resp.content.decode('utf-8'))
    
    def test_user_amr_home(self):
        self.client.login(username='Test', password='password')
        resp = self.client.get(reverse('geneseekr:amr_home'))
        self.assertEqual(resp.status_code, 200)
        amr_requests = AMRSummary.objects.filter()
        for request in amr_requests:
            self.assertNotIn(request.name, resp.content.decode('utf-8'))

    def test_amr_result_login_required(self):
        resp = self.client.get(reverse('geneseekr:amr_result', kwargs={'amr_request_pk': 1}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_amr_result_404_no_run(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('geneseekr:amr_result', kwargs={'amr_request_pk': 123}))
        self.assertEqual(resp.status_code, 404)

# Prokka View Tests ----------------------------------------------------------------------------------------------------------------->

    def test_prokka_home_login_required(self):
        resp = self.client.get(reverse('geneseekr:prokka_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_prokka_home(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('geneseekr:prokka_home'))
        self.assertEqual(resp.status_code, 200)
        prokka_requests = ProkkaRequest.objects.filter()
        for request in prokka_requests:
            self.assertIn(request.name, resp.content.decode('utf-8'))
    
    def test_user_prokka_home(self):
        self.client.login(username='Test', password='password')
        resp = self.client.get(reverse('geneseekr:prokka_home'))
        self.assertEqual(resp.status_code, 200)
        prokka_requests = ProkkaRequest.objects.filter()
        for request in prokka_requests:
            self.assertNotIn(request.name, resp.content.decode('utf-8'))

    def test_prokka_result_login_required(self):
        resp = self.client.get(reverse('geneseekr:prokka_result', kwargs={'prokka_request_pk': 1}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_prokka_result_404_no_run(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('geneseekr:prokka_result', kwargs={'prokka_request_pk': 123}))
        self.assertEqual(resp.status_code, 404)
