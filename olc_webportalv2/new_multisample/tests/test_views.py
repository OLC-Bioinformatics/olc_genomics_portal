from django.test import TestCase, Client
from django.urls import reverse
from olc_webportalv2.users.models import User
from olc_webportalv2.new_multisample.models import ProjectMulti, Sample


class SampleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username='TestUser')
        user.set_password('password')
        user.save()
        ProjectMulti.objects.create(project_title='FakeProject',
                                    description='A project used for unit tests.',
                                    user=user)
        project = ProjectMulti.objects.get(project_title='FakeProject')
        Sample.objects.create(title='FakeSample',
                              project=project)
        wrong_user = User.objects.create(username='BadUser')
        wrong_user.set_password('password')
        wrong_user.save()

    def test_login_required_project_detail(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        resp = self.client.get(reverse('new_multisample:project_detail', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_project_detail_loads(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:project_detail', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 200)  # Should get 200 if everything is good with the request

    def test_project_detail_template(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:project_detail', kwargs={'project_id': project.pk}))
        self.assertTemplateUsed(resp, 'new_multisample/project_detail.html')

    def test_wrong_user_project_detail(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:project_detail', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not the one who made the project

    def test_new_multisample_home(self):
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:new_multisample'))
        self.assertEqual(resp.status_code, 200)

    def test_no_login_new_multisample_home(self):
        resp = self.client.get(reverse('new_multisample:new_multisample'))
        self.assertEqual(resp.status_code, 302)  # Redirect if not logged in.

    def test_upload_sample_loads(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:upload_samples', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new_multisample/upload_samples.html')

    def test_upload_sample_no_login(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        resp = self.client.get(reverse('new_multisample:upload_samples', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)  # Redirect if not logged in.

    def test_upload_wrong_user(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:upload_samples', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)  # Wrong user == redirect to forbidden

    # TODO: More tests on upload - correct files uploaded and all that good stuff.
    def test_genomeqaml_results_loads(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:genomeqaml_results', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new_multisample/genomeqaml_results.html')

    def test_genomeqaml_results_no_login(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        resp = self.client.get(reverse('new_multisample:genomeqaml_results', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_genomeqaml_results_wrong_user(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:genomeqaml_results', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_genomeqaml_detail_loads(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:genomeqaml_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new_multisample/genomeqaml_detail.html')

    def test_genomeqaml_detail_no_login(self):
        sample = Sample.objects.get(title='FakeSample')
        resp = self.client.get(reverse('new_multisample:genomeqaml_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_genomeqaml_detail_wrong_user(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:genomeqaml_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_confindr_results_loads(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:confindr_results_table', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new_multisample/confindr_results_table.html')

    def test_confindr_results_login_required(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        resp = self.client.get(reverse('new_multisample:confindr_results_table', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_confindr_results_wrong_user(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:confindr_results_table', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_project_remove_loads(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:project_remove', kwargs={'project_id': project.pk}))
        self.assertRedirects(resp, '/newmultiprojects/')  # Should get redirected to home page.
        # Also assert that the project object no longer exists.
        self.assertRaises(ProjectMulti.DoesNotExist, ProjectMulti.objects.get, project_title='FakeProject')

    def test_project_remove_login_required(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        resp = self.client.get(reverse('new_multisample:project_remove', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)  # Redirect should occur.
        # Also make sure the project still exists and hasn't been deleted.
        num_projects = len(ProjectMulti.objects.filter(project_title='FakeProject'))
        self.assertEqual(num_projects, 1)

    def test_remove_wrong_user(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:project_remove', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)  # Redirect should occur.
        # Also make sure the project still exists and hasn't been deleted.
        num_projects = len(ProjectMulti.objects.filter(project_title='FakeProject'))
        self.assertEqual(num_projects, 1)

    def test_project_remove_confirm_works(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:project_remove_confirm', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new_multisample/confirm_project_delete.html')

    def test_project_remove_confirm_login_required(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        resp = self.client.get(reverse('new_multisample:project_remove_confirm', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_project_remove_confirm_wrong_user(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:project_remove_confirm', kwargs={'project_id': project.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_sample_remove_works(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:sample_remove', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)  # Should get redirected, so this is OK.
        # Make sure sample has been deleted.
        self.assertRaises(Sample.DoesNotExist, Sample.objects.get, title='FakeSample')

    def test_sample_remove_login_required(self):
        sample = Sample.objects.get(title='FakeSample')
        resp = self.client.get(reverse('new_multisample:sample_remove', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)  # Should get redirected, so this is OK.
        # Make sure sample has NOT been deleted.
        num_samples = len(Sample.objects.filter(title='FakeSample'))
        self.assertEqual(num_samples, 1)

    def test_sample_remove_wrong_user(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:sample_remove', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)  # Should get redirected, so this is OK.
        # Make sure sample has NOT been deleted.
        num_samples = len(Sample.objects.filter(title='FakeSample'))
        self.assertEqual(num_samples, 1)

    def test_sample_remove_confirm_works(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:sample_remove_confirm', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new_multisample/confirm_sample_delete.html')

    def test_sample_remove_confirm_login_required(self):
        sample = Sample.objects.get(title='FakeSample')
        resp = self.client.get(reverse('new_multisample:sample_remove_confirm', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_sample_remove_confirm_wrong_user(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:sample_remove_confirm', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_gdcs_detail_load(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:gdcs_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new_multisample/gdcs_detail.html')

    def test_gdcs_detail_login_required(self):
        sample = Sample.objects.get(title='FakeSample')
        resp = self.client.get(reverse('new_multisample:gdcs_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_gdcs_detail_wrong_user(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:gdcs_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_amr_detail_load(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='TestUser', password='password')
        resp = self.client.get(reverse('new_multisample:amr_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new_multisample/amr_detail.html')

    def test_amr_detail_login_required(self):
        sample = Sample.objects.get(title='FakeSample')
        resp = self.client.get(reverse('new_multisample:amr_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_amr_detail_wrong_user(self):
        sample = Sample.objects.get(title='FakeSample')
        self.client.login(username='BadUser', password='password')
        resp = self.client.get(reverse('new_multisample:amr_detail', kwargs={'sample_id': sample.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_file_upload_bad_file_extension(self):
        project = ProjectMulti.objects.get(project_title='FakeProject')
        self.client.login(username='TestUser', password='password')

