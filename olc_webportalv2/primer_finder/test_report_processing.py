import json
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from olc_webportalv2.primer_finder.methods import (
    _upload_summary_excel,
    calculate_panel_percentage_from_counts,
    create_details,
    process_sequence_data_from_report,
    sort_mismatch_details,
    validator_sample_results,
)
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorPanel,
    ValidatorPrimers,
    ValidatorPrimerSet,
    ValidatorRequest,
    ValidatorSample,
    ValidatorTarget,
    VerifierPanel,
    VerifierPrimerSet,
)
from olc_webportalv2.primer_finder.validator_gene_reports import (
    categorize_gene_hits,
)
from olc_webportalv2.primer_finder.views import primer_validator_report
from olc_webportalv2.users.models import User


class ValidatorReportViewTests(unittest.TestCase):
    @patch('olc_webportalv2.primer_finder.views.render')
    @patch(
        'olc_webportalv2.primer_finder.views.validator_sample_results',
        return_value={'sample-a': {'classification': 'positive'}},
    )
    @patch('olc_webportalv2.primer_finder.views.ValidatorPanel.objects.filter')
    @patch(
        'olc_webportalv2.primer_finder.views.ValidatorPrimerSet.objects.filter'
    )
    @patch('olc_webportalv2.primer_finder.views.get_object_or_404')
    def test_report_context_contains_summary_panels_and_sample_results(
        self,
        get_object,
        filter_primers,
        filter_panels,
        sample_results,
        render,
    ):
        user = SimpleNamespace(is_authenticated=True)
        request = RequestFactory().get(
            '/primer_finder/primer_validator_report/721/'
        )
        request.user = user
        validator_request = SimpleNamespace(
            pk=721,
            summary='{"primer": "summary"}',
            totals='{"samples": 1}',
            forward_primer='>forward\nACGT',
            reverse_primer='>reverse\nTGCA',
        )
        get_object.return_value = validator_request
        filter_primers.return_value = [
            SimpleNamespace(primer_name='1-gene'),
        ]
        filter_panels.return_value = [
            SimpleNamespace(panel='inclusivity', genus='Listeria'),
        ]
        render.return_value = HttpResponse()

        response = primer_validator_report(request, validator_pk=721)

        self.assertEqual(response.status_code, 200)
        get_object.assert_called_once_with(ValidatorRequest, pk=721, user=user)
        sample_results.assert_called_once_with(validator_request)
        context = render.call_args.args[2]
        self.assertEqual(context['mismatch_details'], {'primer': 'summary'})
        self.assertEqual(context['totals'], {'samples': 1})
        self.assertEqual(
            context['sample_results'],
            {
                'sample-a': {'classification': 'positive'},
            },
        )
        self.assertEqual(context['clean_primers'], {'1-gene': 'a1gene'})
        self.assertEqual(context['forward'], 'ACGT')
        self.assertEqual(context['reverse'], 'TGCA')


class PrimerFinderReportAggregationTests(unittest.TestCase):
    def test_sequence_report_updates_genus_and_combined_mismatch_totals(self):
        details = {'invA': {'inclusivity': {}}}
        totals = {'invA': {'inclusivity': {}}}
        sequence_data = {
            'amplicon_length': 100,
            'total_mismatch': '1',
            'forward_mismatch': '1',
            'forward_mismatch_details': '1:A>G',
            'reverse_mismatch': '',
            'reverse_mismatch_details': '',
        }

        for _ in range(2):
            totals, details, hit = process_sequence_data_from_report(
                details=details,
                sequence_data=sequence_data,
                primer_name='invA',
                panel_name='inclusivity',
                genus='listeria',
                totals=totals,
            )
            self.assertTrue(hit)

        for location in ('listeria', 'combined'):
            mismatch_counts = totals['invA']['inclusivity'][location][
                'total_mismatch'
            ][1]
            self.assertEqual(mismatch_counts['totals'], 2)
            self.assertEqual(mismatch_counts['1:A>G, 0'], 2)
            self.assertEqual(
                details['invA']['inclusivity'][location]['forward_mismatches'][
                    '1'
                ],
                2,
            )

    def test_sequence_without_amplicon_is_not_counted_as_a_hit(self):
        details = {'invA': {'inclusivity': {}}}
        totals = {'invA': {'inclusivity': {}}}

        result = process_sequence_data_from_report(
            details=details,
            sequence_data={'amplicon_length': ''},
            primer_name='invA',
            panel_name='inclusivity',
            genus='listeria',
            totals=totals,
        )

        self.assertEqual(result, (totals, details, False))
        self.assertEqual(totals['invA']['inclusivity'], {})

    def test_panel_percentages_are_rounded_and_zero_safe(self):
        self.assertEqual(
            calculate_panel_percentage_from_counts(
                panel_total=3,
                panel_positive=2,
            ),
            {'percent': 66.67, 'positive': 2, 'total': 3},
        )
        self.assertEqual(
            calculate_panel_percentage_from_counts(
                panel_total=0,
                panel_positive=0,
            ),
            {'percent': 0.0, 'positive': 0, 'total': 0},
        )

    def test_equal_mismatch_counts_are_sorted_deterministically(self):
        details = {
            'invA': {
                'inclusivity': {
                    'listeria': {
                        'forward_details': {
                            'C>T': 1,
                            'A>G': 1,
                        },
                    },
                },
            },
        }

        sort_mismatch_details(details=details)

        self.assertEqual(
            details['invA']['inclusivity']['listeria']['forward_details'],
            [('A>G', 1), ('C>T', 1)],
        )

    def test_gene_hit_classifier_reports_premature_internal_stops(self):
        categories = categorize_gene_hits(
            [
                {
                    'report': 'sample_blastn_geneseekr.tsv',
                    'query': 'contig-1.fasta',
                    'target': 'invA',
                    'identity': '99',
                    'query_start': '1',
                    'query_end': '9',
                    'query_frame': '1',
                    'query_sequence': 'ATGTAAATG',
                    'subject_start': '1',
                    'subject_end': '9',
                    'subject_length': '9',
                    'subject_sequence': 'ATGCAAATG',
                }
            ]
        )

        self.assertEqual(categories, {'sample': ['INTERNAL_STOP']})

    def test_gene_hit_classifier_detects_extended_terminal_reference(self):
        record = {
            'report': 'sample_blastn_geneseekr.tsv',
            'query': 'contig-1.fasta',
            'target': 'invA',
            'identity': '99',
            'query_sequence': 'ATGCAAAAA',
            'subject_start': '1',
            'subject_end': '9',
            'subject_length': '9',
            'subject_sequence': 'ATGCAATAA',
        }

        self.assertEqual(
            categorize_gene_hits([record]),
            {'sample': ['EXTENDED']},
        )

        record['query_sequence'] = 'ATGCAATAA'
        self.assertEqual(categorize_gene_hits([record]), {})

    def test_gene_classifier_detects_adjacent_fragments_in_different_frames(
        self,
    ):
        records = [
            {
                'report': 'sample_blastn_geneseekr.tsv',
                'query': 'contig-1',
                'target': 'invA',
                'identity': '99',
                'query_start': '10',
                'query_end': '39',
                'query_length': '100',
                'query_sequence': 'ATG' * 10,
                'subject_start': '1',
                'subject_end': '30',
                'subject_length': '100',
            },
            {
                'report': 'sample_blastn_geneseekr.tsv',
                'query': 'contig-2',
                'target': 'invA',
                'identity': '99',
                'query_start': '41',
                'query_end': '70',
                'query_length': '100',
                'query_sequence': 'ATG' * 10,
                'subject_start': '31',
                'subject_end': '60',
                'subject_length': '100',
            },
        ]

        self.assertEqual(
            categorize_gene_hits(records),
            {'sample': ['FRAME_SHIFT']},
        )

    def test_gene_hit_classifier_applies_identity_and_target_filters(self):
        record = {
            'report': 'sample_blastn_geneseekr.tsv',
            'query': 'contig-1.fasta',
            'target': 'invA',
            'identity': '89',
            'query_sequence': 'ATGTAAATG',
            'subject_start': '1',
            'subject_end': '9',
            'subject_length': '9',
        }

        self.assertEqual(categorize_gene_hits([record]), {})
        self.assertEqual(
            categorize_gene_hits([record], identity_cutoff=80),
            {'sample': ['INTERNAL_STOP']},
        )
        self.assertEqual(
            categorize_gene_hits([record], allowed_targets=['stn']),
            {},
        )


class ValidatorSampleClassificationTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='sample-classification-user')
        self.request = ValidatorRequest.objects.create(
            user=user,
            project_name='sample-classification',
            forward_primer='ACGT',
            reverse_primer='TGCA',
        )
        ValidatorPanel.objects.create(
            validator_request=self.request,
            panel='inclusivity',
            genus='Listeria',
        )

    def add_sample(self, name, *, logic='AND', classification_rule='none'):
        return ValidatorSample.objects.create(
            validator_request=self.request,
            name=name,
            logic=logic,
            classification_rule=classification_rule,
        )

    def add_target(
        self,
        sample,
        name,
        *,
        probe_sequence='',
        probe_evaluation_method='percent_identity',
        probe_percent_identity=80,
        probe_max_differences=2,
    ):
        return ValidatorTarget.objects.create(
            sample=sample,
            name=name,
            forward_primer='ACGT',
            reverse_primer='TGCA',
            probe_sequence=probe_sequence,
            probe_evaluation_method=probe_evaluation_method,
            probe_percent_identity=probe_percent_identity,
            probe_max_differences=probe_max_differences,
        )

    def set_report(self, report):
        self.request.report = json.dumps(report)
        self.request.save(update_fields=['report'])

    def test_and_or_logic_classifies_each_reported_sequence(self):
        and_sample = self.add_sample('sample-and', logic='AND')
        or_sample = self.add_sample('sample-or', logic='OR')
        for sample in (and_sample, or_sample):
            self.add_target(sample, 'invA')
            self.add_target(sample, 'stn')
        self.set_report(
            {
                'invA': {
                    'inclusivity': {
                        'seq-b': {'amplicon_length': 100},
                    },
                },
                'stn': {
                    'inclusivity': {
                        'seq-a': {'amplicon_length': 80},
                    },
                },
            }
        )

        results = validator_sample_results(self.request)
        classifications = {
            (result['sample'], result['seqid']): result['classification']
            for result in results
        }

        self.assertEqual(
            classifications,
            {
                ('sample-and', 'seq-a'): 'Not detected',
                ('sample-and', 'seq-b'): 'Not detected',
                ('sample-or', 'seq-a'): 'Detected',
                ('sample-or', 'seq-b'): 'Detected',
            },
        )

    def test_salmonella_rule_requires_a_marker_target(self):
        sample = self.add_sample(
            'sample-salmonella',
            classification_rule='salmonella',
        )
        self.add_target(sample, 'InvA')
        self.add_target(sample, 'other')
        self.set_report(
            {
                'InvA': {
                    'inclusivity': {
                        'marker-seqid': {'amplicon_length': 100},
                    },
                },
                'other': {
                    'inclusivity': {
                        'non-marker-seqid': {'amplicon_length': 100},
                    },
                },
            }
        )

        results = validator_sample_results(self.request)

        self.assertEqual(
            [result['classification'] for result in results],
            ['Salmonella', 'Not Salmonella'],
        )

    def test_probe_thresholds_accept_boundary_and_reject_low_quality_hits(
        self,
    ):
        identity_sample = self.add_sample('sample-identity', logic='OR')
        differences_sample = self.add_sample('sample-differences', logic='OR')
        self.add_target(
            identity_sample,
            'invA',
            probe_sequence='ACGT',
            probe_percent_identity=95,
        )
        self.add_target(
            differences_sample,
            'stn',
            probe_sequence='ACGT',
            probe_evaluation_method='max_differences',
            probe_max_differences=2,
        )
        self.set_report(
            {
                'invA': {
                    'inclusivity': {
                        'identity-boundary': {
                            'amplicon_length': 100,
                            'probe': {'probe': {'percent_id': 95}},
                        },
                        'identity-low': {
                            'amplicon_length': 100,
                            'probe': {'probe': {'percent_id': 94.99}},
                        },
                        'identity-invalid': {
                            'amplicon_length': 100,
                            'probe': {'probe': {'percent_id': 'invalid'}},
                        },
                    },
                },
                'stn': {
                    'inclusivity': {
                        'differences-boundary': {
                            'amplicon_length': 100,
                            'probe': {
                                'stn': {'mismatches': 1, 'gaps': 1},
                            },
                        },
                        'differences-over': {
                            'amplicon_length': 100,
                            'probe': {
                                'other': {'mismatches': 0, 'gaps': 0},
                            },
                        },
                    },
                },
            }
        )

        results = validator_sample_results(self.request)
        classifications = {
            (result['sample'], result['seqid']): result['classification']
            for result in results
        }

        self.assertEqual(
            classifications,
            {
                ('sample-identity', 'identity-boundary'): 'Detected',
                ('sample-identity', 'identity-invalid'): 'Not detected',
                ('sample-identity', 'identity-low'): 'Not detected',
                ('sample-differences', 'differences-boundary'): 'Detected',
                ('sample-differences', 'differences-over'): 'Not detected',
            },
        )

    def test_invalid_report_json_returns_no_sample_results(self):
        self.request.report = '{invalid'

        self.assertEqual(validator_sample_results(self.request), [])


class PrimerReportGenerationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='report-generation-user')

    def test_verifier_report_persists_aggregates_and_uploads_summary(self):
        request = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name='verifier-report',
            report=json.dumps(
                {
                    'invA': {
                        'inclusivity': {
                            'seq-a': {
                                'amplicon_length': 100,
                                'total_mismatch': '1',
                                'forward_mismatch': '1',
                                'forward_mismatch_details': '1:A>G',
                            },
                            'seq-b': {'amplicon_length': ''},
                        },
                    },
                }
            ),
        )
        VerifierPrimerSet.objects.create(
            verifier_request=request,
            primer_name='invA',
        )
        VerifierPanel.objects.create(
            verifier_request=request,
            panel='inclusivity',
            genus='Listeria',
        )

        with (
            patch(
                'olc_webportalv2.primer_finder.methods.retrieve_panel_seqids',
                return_value={'listeria': ['seq-a', 'seq-b', 'seq-c']},
            ),
            patch('olc_webportalv2.primer_finder.methods.find_shared_seqids'),
            patch(
                'olc_webportalv2.primer_finder.methods.'
                'create_blob_from_summary'
            ) as upload_summary,
        ):
            result = create_details(
                request=request,
                primer_set_model=VerifierPrimerSet,
                panel_model=VerifierPanel,
            )

        summary = json.loads(result.summary)
        genus_summary = summary['invA']['inclusivity']['listeria']
        self.assertEqual(
            (
                genus_summary['percent'],
                genus_summary['positive'],
                genus_summary['total'],
            ),
            (33.33, 1, 3),
        )
        self.assertEqual(
            genus_summary['forward_mismatches'],
            [['1', 1]],
        )
        totals = json.loads(result.totals)
        mismatch_counts = totals['invA']['inclusivity']['combined'][
            'total_mismatch'
        ]['1']
        self.assertEqual(mismatch_counts['totals'], 1)
        upload_summary.assert_called_once_with(request=result)

    def test_validator_probe_threshold_controls_summary_detection(self):
        request = ValidatorRequest.objects.create(
            user=self.user,
            project_name='validator-probe-report',
            forward_primer='ACGT',
            reverse_primer='TGCA',
            benchmark_versions={'bds-listeria': '1.0.0'},
            report=json.dumps(
                {
                    'invA': {
                        'inclusivity': {
                            'accepted': {
                                'amplicon_length': 100,
                                'probe': {
                                    'invA': {'mismatches': 1, 'gaps': 0},
                                },
                            },
                            'rejected': {
                                'amplicon_length': 100,
                                'probe': {
                                    'probe': {'mismatches': 2, 'gaps': 0},
                                },
                            },
                        },
                    },
                }
            ),
        )
        sample = ValidatorSample.objects.create(
            validator_request=request,
            name='sample',
            logic='OR',
        )
        ValidatorTarget.objects.create(
            sample=sample,
            name='invA',
            forward_primer='ACGT',
            reverse_primer='TGCA',
            probe_sequence='ACGT',
            probe_evaluation_method='max_differences',
            probe_max_differences=1,
        )
        ValidatorPrimerSet.objects.create(
            validator_request=request,
            primer_name='invA',
        )
        ValidatorPanel.objects.create(
            validator_request=request,
            panel='inclusivity',
            genus='Listeria',
        )

        with (
            patch(
                'olc_webportalv2.primer_finder.methods.retrieve_panel_seqids',
                return_value={
                    'listeria': ['accepted', 'rejected', 'missing'],
                },
            ),
            patch('olc_webportalv2.primer_finder.methods.find_shared_seqids'),
            patch(
                'olc_webportalv2.primer_finder.methods.'
                'create_blob_from_summary'
            ),
        ):
            result = create_details(
                request=request,
                primer_set_model=ValidatorPrimerSet,
                panel_model=ValidatorPanel,
                analysis='validator',
            )

        summary = json.loads(result.summary)
        genus_summary = summary['invA']['inclusivity']['listeria']
        self.assertEqual(
            (
                genus_summary['percent'],
                genus_summary['positive'],
                genus_summary['total'],
            ),
            (33.33, 1, 3),
        )
        self.assertEqual(
            genus_summary['probe_prevalence'],
            {
                'percent': 33.33,
                'positive': 1,
                'negative': 2,
                'total': 3,
            },
        )
        self.assertEqual(
            summary['_metadata'],
            {'benchmark_versions': {'bds-listeria': '1.0.0'}},
        )


class PrimerSummaryExcelTests(TestCase):
    def test_summary_workbook_contains_report_rows_and_reconstructed_alignment(
        self,
    ):
        from openpyxl import load_workbook

        user = User.objects.create_user(username='summary-excel-test')
        request = ValidatorRequest.objects.create(
            user=user,
            project_name='summary test',
            forward_primer='ACGT',
            reverse_primer='TGCA',
            probe_sequence='ACGT',
            report=json.dumps(
                {
                    'validator_version': '2.0',
                    'invA': {
                        'INCLUSIVITY': {
                            'positive': 1,
                            'total': 1,
                            'percent': 100,
                            '2024-ABC-0001': {
                                'forward_mismatch': 1,
                                'forward_mismatch_details': '2C&gt;G',
                                'reverse_mismatch': 0,
                                'reverse_mismatch_details': '',
                                'amplicon_length': 4,
                                'contig': 'contig-1',
                                'start_pos': 5,
                                'stop_pos': 8,
                                'direction': 'forward',
                                'probe': {
                                    'invA': {
                                        'percent_id': 95,
                                        'gaps': 0,
                                        'mismatches': 1,
                                        'mismatch_details': '4T>A',
                                    },
                                },
                            },
                        }
                    },
                }
            ),
        )
        primer_set = ValidatorPrimerSet.objects.create(
            validator_request=request,
            primer_name='invA',
        )
        ValidatorPrimers.objects.create(
            primer=primer_set,
            primer_header='invA-F',
            primer_sequence='ACGT',
        )
        ValidatorPrimers.objects.create(
            primer=primer_set,
            primer_header='invA-R',
            primer_sequence='TGCA',
        )
        blob_client = mock.Mock()
        workbook_data = {}

        def capture_uploaded_workbook(container, blob_name, file_path, **kwargs):
            workbook = load_workbook(
                file_path, read_only=True, data_only=True
            )
            workbook_data['sheetnames'] = workbook.sheetnames
            worksheet = workbook['PrimerVerifierResults']
            workbook_data['headers'] = [
                cell.value for cell in worksheet[1]
            ]
            workbook_data['row'] = [cell.value for cell in worksheet[2]]
            alignment_worksheet = workbook['Pseudoalignments']
            workbook_data['alignment_headers'] = [
                cell.value for cell in alignment_worksheet[1]
            ]
            workbook_data['alignment_row'] = [
                cell.value for cell in alignment_worksheet[2]
            ]
            workbook_data['audit_headers'] = [
                cell.value for cell in workbook['GeneCategoryAudit'][1]
            ]
            workbook.close()

        blob_client.upload_file.side_effect = capture_uploaded_workbook
        _upload_summary_excel(blob_client, 'primer-validator-1', request)

        blob_client.upload_file.assert_called_once()
        headers = workbook_data['headers']
        row = workbook_data['row']

        self.assertEqual(
            workbook_data['sheetnames'],
            [
                'PrimerVerifierResults',
                'Pseudoalignments',
                'GeneCategoryAudit',
            ],
        )
        self.assertEqual(row[:2], ['2024-ABC-0001', '2024-ABC-0001'])
        self.assertEqual(row[5], 'INCLUSIVITY')
        self.assertEqual(len(headers), 26)
        self.assertIn('amplicon length  invA', headers)
        self.assertIn('location  invA', headers)
        self.assertIn('Probe Presence  invA', headers)
        self.assertEqual(row[headers.index('Probe Presence  invA')], 'Present')
        self.assertEqual(len(workbook_data['audit_headers']), 21)
        self.assertEqual(
            workbook_data['alignment_headers'],
            [
                'PANEL',
                'SEQID',
                'Primer',
                'Forward Mismatch',
                'Forward Mismatch Details',
                'Reverse Mismatch',
                'Reverse Mismatch Details',
                'Total Mismatches',
                'Pseudoalignment',
            ],
        )
        alignment = workbook_data['alignment_row'][-1]
        self.assertEqual(
            workbook_data['alignment_row'][:3],
            ['INCLUSIVITY', '2024-ABC-0001', 'invA'],
        )
        self.assertIn('FORWARD', alignment)
        self.assertIn('Target: AGGT', alignment)
        self.assertIn('REVERSE', alignment)
        self.assertEqual(
            blob_client.upload_file.call_args.args[:2],
            (
                'primer-validator-1',
                'reports/primer-validator-1_summary_report.xlsx',
            ),
        )

    def test_summary_workbook_skips_absent_or_invalid_report_json(self):
        blob_client = mock.Mock()

        for report in ('', '{invalid'):
            with self.subTest(report=report):
                request = SimpleNamespace(pk=1, report=report)
                _upload_summary_excel(blob_client, 'primer-run', request)

        blob_client.upload_file.assert_not_called()

    def test_summary_workbook_resolves_selected_benchmarks_per_sequence(self):
        from openpyxl import load_workbook

        user = User.objects.create_user(username='summary-benchmark-test')
        report = {
            'HLY': {
                'inclusivity': {
                    'BDS-LM001': {
                        'amplicon_length': '179',
                        'probe': {
                            'HLY': {
                                'percent_id': 100.0,
                                'gaps': 0,
                                'mismatches': 0,
                                'mismatch_details': '',
                            },
                        },
                    },
                    'GCA_000000001': {
                        'amplicon_length': '179',
                        'probe': {
                            'HLY': {
                                'percent_id': 100.0,
                                'gaps': 0,
                                'mismatches': 0,
                                'mismatch_details': '',
                            },
                        },
                    },
                },
            },
        }
        request = ValidatorRequest.objects.create(
            user=user,
            project_name='summary benchmark test',
            forward_primer='ACGT',
            reverse_primer='TGCA',
            probe_sequence='ACGT',
            inclusivity_panel=['listeria', 'bds-listeria'],
            benchmark_versions={'bds-listeria': '1.0.0'},
            validator_original_report=json.dumps(report),
        )
        workbook_rows = {}
        blob_client = mock.Mock()

        def capture_uploaded_workbook(container, blob_name, file_path, **kwargs):
            workbook = load_workbook(
                file_path, read_only=True, data_only=True
            )
            worksheet = workbook['PrimerVerifierResults']
            headers = [
                cell.value for cell in next(worksheet.iter_rows(max_row=1))
            ]
            for values in worksheet.iter_rows(min_row=2, values_only=True):
                workbook_rows[values[1]] = dict(zip(headers, values))
            workbook.close()

        blob_client.upload_file.side_effect = capture_uploaded_workbook
        _upload_summary_excel(blob_client, 'primer-validator-benchmark', request)

        self.assertEqual(
            workbook_rows['BDS-LM001']['BENCHMARK PANEL'],
            'BDS-Listeria',
        )
        self.assertEqual(
            workbook_rows['BDS-LM001']['Benchmark Version'],
            '1.0.0',
        )
        self.assertEqual(
            workbook_rows['GCA_000000001']['BENCHMARK PANEL'],
            'Listeria',
        )
        self.assertEqual(
            workbook_rows['GCA_000000001']['Benchmark Version'],
            'ND',
        )

    def test_summary_workbook_includes_validator_logic_and_gene_audit(self):
        from openpyxl import load_workbook

        user = User.objects.create_user(username='summary-audit-test')
        seqid = '2024-ABC-0002'
        report = {
            'invA': {
                'INCLUSIVITY': {
                    seqid: {
                        'amplicon_length': 100,
                        'probe': {
                            'invA': {
                                'percent_id': 40,
                                'gaps': 0,
                                'mismatches': 1,
                            },
                        },
                    },
                },
            },
            'stn': {
                'INCLUSIVITY': {
                    seqid: {
                        'amplicon_length': 100,
                        'probe': {
                            'stn': {
                                'percent_id': 95,
                                'gaps': 0,
                                'mismatches': 0,
                            },
                        },
                    },
                },
            },
        }
        request = ValidatorRequest.objects.create(
            user=user,
            project_name='summary audit test',
            forward_primer='ACGT',
            reverse_primer='TGCA',
            report=json.dumps(report),
            gene_exclusion_categories=['INTERNAL_STOP'],
            gene_exclusion_results={seqid: ['INTERNAL_STOP']},
            gene_exclusion_details={
                'invA-report': {
                    'invA': [
                        {
                            'category': 'INTERNAL_STOP',
                            'reason': 'Internal stop codon',
                            'hit': {
                                'query': seqid,
                                'identity': 98.5,
                                'coverage': 100,
                                'orientation': 'forward',
                                'subject_start': 1,
                                'subject_end': 90,
                                'subject_length': 90,
                                'query_start': 1,
                                'query_end': 90,
                                'bit_score': 120,
                            },
                            'stop_evidence': [
                                {
                                    'subject_location': '30',
                                    'codon_change': 'TGG>TGA',
                                },
                            ],
                            'second_hit': {
                                'query': 'paired-query',
                                'identity': 95,
                                'subject_start': 1,
                                'subject_end': 90,
                            },
                        },
                    ],
                },
            },
            gene_results=[
                {
                    'report': f'{seqid}_blastn_geneseekr.tsv',
                    'target': 'invA',
                    'percent_identity': 98.5,
                },
            ],
        )
        sample = ValidatorSample.objects.create(
            validator_request=request,
            name='sample-and',
            logic='AND',
        )
        ValidatorTarget.objects.create(
            sample=sample,
            name='invA',
            probe_sequence='ACGT',
            probe_evaluation_method='max_differences',
            probe_max_differences=1,
            gene_sequence='ATG',
        )
        ValidatorTarget.objects.create(
            sample=sample,
            name='stn',
            probe_sequence='TGCA',
            probe_evaluation_method='percent_identity',
            probe_percent_identity=80,
        )

        workbook_data = {}
        blob_client = mock.Mock()

        def capture_uploaded_workbook(container, blob_name, file_path, **kwargs):
            workbook = load_workbook(
                file_path, read_only=True, data_only=True
            )
            result_sheet = workbook['PrimerVerifierResults']
            result_headers = [cell.value for cell in result_sheet[1]]
            result_row = [cell.value for cell in result_sheet[2]]
            audit_sheet = workbook['GeneCategoryAudit']
            audit_row = [cell.value for cell in audit_sheet[2]]
            workbook_data.update(
                result_headers=result_headers,
                result_row=result_row,
                audit_row=audit_row,
            )
            workbook.close()

        blob_client.upload_file.side_effect = capture_uploaded_workbook
        _upload_summary_excel(blob_client, 'primer-validator-2', request)

        headers = workbook_data['result_headers']
        row = workbook_data['result_row']
        self.assertEqual(row[headers.index('RESULT')], 'Detected')
        self.assertEqual(
            row[headers.index('LOGIC')],
            'Pass: All targets matched',
        )
        self.assertEqual(
            row[headers.index('Matched Exclusion Categories')],
            'INTERNAL_STOP',
        )
        self.assertEqual(
            row[headers.index('Gene Sequence Percent Match  invA')],
            98.5,
        )
        self.assertEqual(
            workbook_data['audit_row'][:6],
            [
                'invA-report',
                'invA',
                'INTERNAL_STOP',
                'Internal stop codon',
                seqid,
                98.5,
            ],
        )
