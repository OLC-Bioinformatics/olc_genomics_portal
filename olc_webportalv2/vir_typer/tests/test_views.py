#!/usr/bin/env python3
from olc_webportalv2.users.models import User
from django.test import TestCase
from django.urls import reverse
import json
import os

from olc_webportalv2.vir_typer.views import parse_report, sequence_consensus, sequence_html_string
from olc_webportalv2.vir_typer.models import VirTyperFiles, VirTyperProject, VirTyperRequest, VirTyperResults
__author__ = 'adamkoziol'


class SampleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username='TestViews')
        user.set_password('password')
        user.save()
        project = VirTyperProject.objects.create(project_name='test_views',
                                                 user=user)
        project.save()
        # Sample 1
        request = VirTyperRequest.objects.create(project_name=project,
                                                 lab_ID='St. Hyacinthe',
                                                 isolate_source='huître',
                                                 LSTS_ID='2019FPP00000475214',
                                                 putative_classification='Norovirus genogroup 1',
                                                 sample_name='VI482',
                                                 subunit=1,
                                                 date_received='2019-08-01',
                                                 analyst_name='Rachel'
                                                 )
        request.save()
        files = VirTyperFiles.objects.create(sample_name=request,
                                             sequence_file='P19954_2019_VI482_11_GI_B05_M13-R17_G10_068.ab1')
        files.save()
        files = VirTyperFiles.objects.create(sample_name=request,
                                             sequence_file='P19954_2019_VI482_11_GI_B04_M13-R17_F10_070.ab1')
        files.save()
        files = VirTyperFiles.objects.create(sample_name=request,
                                             sequence_file='P19954_2019_VI482_11_GI_B03_M13-R17_E10_072.ab1')
        files.save()
        request = VirTyperRequest.objects.create(project_name=project,
                                                 lab_ID='Burnaby',
                                                 isolate_source='Oyster',
                                                 LSTS_ID='2019FPP00000475215',
                                                 putative_classification='Norovirus genogroup 2',
                                                 sample_name='VI483',
                                                 date_received='2019-08-09',
                                                 analyst_name='Rachel'
                                                 )
        request.save()
        # Sample 2
        files = VirTyperFiles.objects.create(sample_name=request,
                                             sequence_file='P19954_2019_VI483_11_GI_B05_M13-R17_G10_074.ab1')
        files.save()

    def test_vir_typer_home_login_required(self):
        resp = self.client.get(reverse('vir_typer:vir_typer_home'))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_vir_typer_home_ok(self):
        self.client.login(username='TestViews', password='password')
        resp = self.client.get(reverse('vir_typer:vir_typer_home'))
        self.assertEqual(resp.status_code, 200)
        vir_typer_requests = VirTyperProject.objects.filter()
        for request in vir_typer_requests:
            self.assertEquals(request.project_name, 'test_views')

    def test_vir_typer_upload_login_required(self):
        resp = self.client.get(reverse('vir_typer:vir_typer_upload', kwargs={'vir_typer_pk': 1}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_vir_typer_upload_404_no_run(self):
        self.client.login(username='TestViews', password='password')
        resp = self.client.get(reverse('vir_typer:vir_typer_upload', kwargs={'vir_typer_pk': 123}))
        self.assertEqual(resp.status_code, 404)

    def test_vir_typer_upload_ok(self):
        self.client.login(username='TestViews', password='password')
        pk = VirTyperProject.objects.get(project_name='test_views').pk
        resp = self.client.get(reverse('vir_typer:vir_typer_upload', kwargs={'vir_typer_pk': pk}))
        self.assertEqual(resp.status_code, 200)

    def test_vir_typer_result_login_required(self):
        resp = self.client.get(reverse('vir_typer:vir_typer_results', kwargs={'vir_typer_pk': 1}))
        self.assertEqual(resp.status_code, 302)  # Should get 302 redirected if user is not logged in.

    def test_vir_typer_result_404_no_run(self):
        self.client.login(username='TestViews', password='password')
        resp = self.client.get(reverse('vir_typer:vir_typer_results', kwargs={'vir_typer_pk': 123}))
        self.assertEqual(resp.status_code, 404)

    def test_vir_typer_result_ok(self):
        project = VirTyperProject.objects.get(project_name='test_views')
        testpath = os.path.abspath(os.path.dirname(__file__))
        json_output = os.path.join(testpath, 'virus_typer_outputs.json')
        with open(json_output, 'r') as json_report:
            project.report = json.load(json_report)
        project.save()
        self.client.login(username='TestViews', password='password')
        resp = self.client.get(reverse('vir_typer:vir_typer_results', kwargs={'vir_typer_pk': project.pk}))
        self.assertEqual(resp.status_code, 200)
        results = VirTyperResults.objects.all()
        self.assertEquals([result.trimmed_quality_stdev for result in results], ['5.59', '6.10','6.23', '5.59'])

    def test_report_parse(self):
        testpath = os.path.abspath(os.path.dirname(__file__))
        json_output = os.path.join(testpath, 'virus_typer_outputs.json')
        with open(json_output, 'r') as json_report:
            json_data = str(json.load(json_report))
        pk = VirTyperProject.objects.get(project_name='test_views').pk
        vir_typer_samples = VirTyperRequest.objects.filter(project_name_id=pk)
        parse_report(vir_typer_json=json_data,
                     vir_typer_samples=vir_typer_samples)
        results = VirTyperResults.objects.all()
        self.assertEquals([result.allele for result in results], ['00069',  '00069','00057', '00069'])

    def test_sequence_html(self):
        vir_typer_pk = VirTyperProject.objects.get(project_name='test_views').pk
        samples = list()
        outputs = list()

        testpath = os.path.abspath(os.path.dirname(__file__))
        json_output = os.path.join(testpath, 'virus_typer_outputs.json')
        with open(json_output, 'r') as json_report:
            json_data = str(json.load(json_report))
        pk = VirTyperProject.objects.get(project_name='test_views').pk
        vir_typer_samples = VirTyperRequest.objects.filter(project_name_id=pk)
        parse_report(vir_typer_json=json_data,
                     vir_typer_samples=vir_typer_samples)

        for sample in VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk):
            samples.append(sample.sample_name)
        for sorted_sample in sorted(samples):
            for sample in VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk):
                if sample.sample_name == sorted_sample:
                    sequences = list()
                    sample_dict = dict()
                    sample_dict['sequence'] = list()
                    for vir_file in VirTyperFiles.objects.filter(sample_name__pk=sample.pk):
                        result = VirTyperResults.objects.filter(sequence_file__id=vir_file.pk)
                        for vir_typer_result in result:
                            seq_identifier_well = os.path.splitext(vir_file.sequence_file)[0].split('_')[-2]
                            seq_identifier_num = os.path.splitext(vir_file.sequence_file)[0].split('_')[-1]
                            seq_identifier_code = '_'.join((seq_identifier_well, seq_identifier_num))
                            sequences.append({sample.sample_name + '_' + seq_identifier_code: vir_typer_result
                                             .trimmed_sequence})
                    consensus_sequence = sequence_consensus(sequences, vir_typer_pk)
                    for vir_file in VirTyperFiles.objects.filter(sample_name__pk=sample.pk):
                        result = VirTyperResults.objects.filter(sequence_file__id=vir_file.pk)
                        for vir_typer_result in result:
                            html_string, variable_locations = sequence_html_string(vir_typer_result.trimmed_sequence,
                                                                                   consensus_sequence)
                            sample_dict['sequence'].append(html_string + '\n')
                    outputs.append(sample_dict)
        for output in outputs:
            self.assertTrue(output['sequence'])
            self.assertTrue(output['sequence'][0].startswith("<span style='color:white;background-color:black;"))
