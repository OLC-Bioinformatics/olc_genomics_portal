from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from olc_webportalv2.primer_finder.forms import (
    PrimerVerifierForm,
    ValidatorExperimentForm,
    ValidatorTargetForm,
    ValidatorTargetFormSetFactory,
)


class PrimerVerifierFormTests(SimpleTestCase):
    def test_valid_fasta_is_canonicalized_and_probe_is_normalized(self):
        form = PrimerVerifierForm(
            data={
                'primer_sequences': (
                    '>gene_F\n acgtacgt \n>gene_R\n tgcatgca\n'
                ),
                'probe_sequence': ' ac gt ',
                'mismatches': '2',
                'min_amplicon_size': '0',
                'max_amplicon_size': '1500',
                'range_buffer': '0',
                'inclusivity_panel': ['listeria'],
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['primer_list'], ['gene'])
        self.assertEqual(
            form.cleaned_data['primer_sequences'],
            '>gene-F\r\nacgtacgt\r\n>gene-R\r\ntgcatgca\r\n',
        )
        self.assertEqual(form.cleaned_data['probe_sequence'], 'acgt')

    def test_fasta_validation_rejects_missing_pair_and_invalid_bases(self):
        form = PrimerVerifierForm(
            data={
                'primer_sequences': '>gene-F\nACGTX\n',
                'mismatches': '2',
                'min_amplicon_size': '0',
                'max_amplicon_size': '1500',
                'range_buffer': '0',
                'inclusivity_panel': ['listeria'],
            }
        )

        self.assertFalse(form.is_valid())
        errors = str(form.errors)
        self.assertIn('illegal character', errors)
        self.assertIn('missing a reverse primer', errors)

    def test_a_panel_is_required_and_exclusivity_only_data_cannot_be_inclusive(
        self,
    ):
        base_data = {
            'primer_sequences': '>gene-F\nACGT\n>gene-R\nTGCA\n',
            'mismatches': '2',
            'min_amplicon_size': '0',
            'max_amplicon_size': '1500',
            'range_buffer': '0',
        }
        no_panel_form = PrimerVerifierForm(data=base_data)
        self.assertFalse(no_panel_form.is_valid())
        self.assertIn('Select at least one', str(no_panel_form.errors))

        overlap_form = PrimerVerifierForm(
            data={
                **base_data,
                'inclusivity_panel': ['listeria'],
                'exclusivity_panel': ['listeria'],
            }
        )
        self.assertFalse(overlap_form.is_valid())
        self.assertIn(
            'cannot be assigned to both panels', str(overlap_form.errors)
        )


class ValidatorFormTests(SimpleTestCase):
    def test_experiment_form_enforces_panel_overlap_and_amplicon_order(self):
        valid = ValidatorExperimentForm(
            data={
                'classification_rule': 'none',
                'inclusivity_panel': ['listeria'],
                'mismatches': '3',
                'min_amplicon_size': '100',
                'max_amplicon_size': '200',
                'range_buffer': '0',
            }
        )
        self.assertTrue(valid.is_valid(), valid.errors)

        overlap = ValidatorExperimentForm(
            data={
                'classification_rule': 'none',
                'inclusivity_panel': ['listeria'],
                'exclusivity_panel': ['listeria'],
                'mismatches': '3',
                'min_amplicon_size': '0',
                'max_amplicon_size': '1500',
                'range_buffer': '0',
            }
        )
        self.assertFalse(overlap.is_valid())
        self.assertIn('both inclusivity and exclusivity', str(overlap.errors))

        invalid_range = ValidatorExperimentForm(
            data={
                'classification_rule': 'none',
                'inclusivity_panel': ['listeria'],
                'mismatches': '3',
                'min_amplicon_size': '201',
                'max_amplicon_size': '200',
                'range_buffer': '0',
            }
        )
        self.assertFalse(invalid_range.is_valid())
        self.assertIn('cannot exceed', str(invalid_range.errors))

    def test_salmonella_rule_disables_sample_logic(self):
        form = ValidatorExperimentForm(
            data={
                'classification_rule': 'salmonella',
                'logic': 'AND',
                'inclusivity_panel': ['listeria'],
                'mismatches': '3',
                'min_amplicon_size': '0',
                'max_amplicon_size': '1500',
                'range_buffer': '0',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['logic'], '')

    def test_target_normalizes_sequences_and_names(self):
        form = ValidatorTargetForm(
            data={
                'sample_name': 'sample 1',
                'target_name': 'invA-target',
                'forward_primer': ' acg t ',
                'reverse_primer': 'tgca',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['sample_name'], 'sample_1')
        self.assertEqual(form.cleaned_data['target_name'], 'invA_target')
        self.assertEqual(form.cleaned_data['forward_primer'], 'ACGT')

    def test_target_rejects_invalid_sequence_bases(self):
        form = ValidatorTargetForm(
            data={
                'target_name': 'invA',
                'forward_primer': 'ACGTZ',
                'reverse_primer': 'TGCA',
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('only A, C, G, T, or N', str(form.errors))

    def test_reference_fasta_is_validated_and_file_handle_is_rewound(self):
        contents = b'>reference-a\nACGTN\n'
        form = ValidatorTargetForm(
            data={
                'target_name': 'invA',
                'forward_primer': 'ACGT',
                'reverse_primer': 'TGCA',
            },
            files={
                'reference_fasta': SimpleUploadedFile(
                    'reference.fasta', contents
                ),
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['reference_fasta'].read(), contents)

        invalid_form = ValidatorTargetForm(
            data={
                'target_name': 'invA',
                'forward_primer': 'ACGT',
                'reverse_primer': 'TGCA',
            },
            files={
                'reference_fasta': SimpleUploadedFile(
                    'invalid.fasta', b'>reference-a\nACGTX\n'
                ),
            },
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn('only A, C, G, T, or N', str(invalid_form.errors))

    def test_formset_rejects_duplicate_targets_and_single_target_logic(self):
        data = {
            'targets-TOTAL_FORMS': '2',
            'targets-INITIAL_FORMS': '0',
            'targets-MIN_NUM_FORMS': '1',
            'targets-MAX_NUM_FORMS': '1000',
        }
        for index, target in enumerate(('invA', 'inva')):
            data.update(
                {
                    f'targets-{index}-sample_name': 'sample',
                    f'targets-{index}-target_name': target,
                    f'targets-{index}-forward_primer': 'ACGT',
                    f'targets-{index}-reverse_primer': 'TGCA',
                }
            )

        duplicate_formset = ValidatorTargetFormSetFactory(
            data, prefix='targets'
        )
        duplicate_formset.classification_rule = 'none'
        duplicate_formset.logic = ''
        self.assertFalse(duplicate_formset.is_valid())
        self.assertIn(
            'Target names must be unique', str(duplicate_formset.errors)
        )

        single_formset = ValidatorTargetFormSetFactory(
            {
                **data,
                'targets-TOTAL_FORMS': '1',
            },
            prefix='targets',
        )
        single_formset.classification_rule = 'none'
        single_formset.logic = 'AND'
        self.assertFalse(single_formset.is_valid())
        self.assertIn(
            'at least two targets per sample',
            str(single_formset.non_form_errors()),
        )
