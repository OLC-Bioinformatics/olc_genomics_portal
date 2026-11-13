import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from olc_webportalv2.primer_finder.methods import (
    calculate_panel_percentage_from_counts,
    create_details,
    process_sequence_data_from_report,
    validator_sample_results,
)
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorPanel,
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
                                'probe': {'mismatches': 1, 'gaps': 1},
                            },
                        },
                        'differences-over': {
                            'amplicon_length': 100,
                            'probe': {
                                'probe': {'mismatches': 2, 'gaps': 1},
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
            report=json.dumps(
                {
                    'invA': {
                        'inclusivity': {
                            'accepted': {
                                'amplicon_length': 100,
                                'probe': {
                                    'probe': {'mismatches': 1, 'gaps': 0},
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

        genus_summary = json.loads(result.summary)['invA']['inclusivity'][
            'listeria'
        ]
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
            {'percent': 33.33, 'positive': 1, 'total': 3},
        )
