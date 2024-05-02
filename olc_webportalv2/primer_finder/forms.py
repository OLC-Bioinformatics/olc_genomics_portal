#!/usr/bin/env python

# Django imports
from django.utils.translation import ugettext_lazy as _
from django.forms import ModelForm
from django import forms

# Standard imports
from io import StringIO

# Third-party imports
from Bio import SeqIO

# Portal-specific imports
from olc_webportalv2.primer_finder.models import genera, PrimerVerifierRequest, \
    ValidatorRequest


mismatch_choices = (
    ('0', _('Zero')),
    ('1', _('One')),
    ('2', _('Two')),
    ('3', _('Three'))
)


class PrimerVerifierForm(ModelForm):
    class Meta:
        model = PrimerVerifierRequest

        fields = [
            'project_name',
            'primer_sequences',
            'mismatches',
            'min_amplicon_size',
            'max_amplicon_size',
            'contig_breaks',
            'range_buffer',
            'inclusivity_panel',
            'exclusivity_panel'
        ]
        labels = {
            'project_name': _('Project Name'),
            'primer_sequences': _('Primer Sequence(s)'),
            'mismatches': _('Maximum Number of Mismatches'),
            'min_amplicon_size': _('Minimum Amplicon Size'),
            'max_amplicon_size': _('Maximum Amplicon Size'),
            'contig_breaks': _('Allow searching for amplicons over multiple contigs?'),
            'range_buffer': _('Minimum distance between amplicons'),
            'inclusivity_panel': _('Genus/Genera to include in inclusivity panel'),
            'exclusivity_panel': _('Genus/Genera to include in exclusivity panel'),

        }
        widgets = {
            'project_name': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       'style': 'max-width: 18em'
                       }
            ),
            'primer_sequences': forms.Textarea(
                attrs={'placeholder': _('>Primer_Sequence1-F \n ACGTACGT... \n >Primer_Sequence1-R \n ACATGCC.... \n '
                                        '>Primer_Sequence2-F \n ....')
                       }
            ),
            'mismatches': forms.Select(
                choices=mismatch_choices,
                attrs={
                    'style': 'max-width: 18em'
                }
            ),
            'min_amplicon_size': forms.TextInput(
                attrs={'style': 'max-width: 18em'
                       }
            ),
            'max_amplicon_size': forms.TextInput(
                attrs={'style': 'max-width: 18em'
                       }
            ),
            'range_buffer': forms.TextInput(
                attrs={'style': 'max-width: 18em'
                       }
            ),
            'inclusivity_panel': forms.CheckboxSelectMultiple(
                choices=genera,
            ),
            'exclusivity_panel': forms.CheckboxSelectMultiple(
                choices=genera,
            ),
        }

    @staticmethod
    def clean_primer_names(primer_name):
        # Replace illegal characters in the record.id with underscores
        for illegal_char in [' ', '-']:
            if illegal_char in primer_name:
                primer_name = primer_name.replace(illegal_char, '_')
                if primer_name.endswith('_F'):
                    primer_name = primer_name.replace('_F', '-F')
                elif primer_name.endswith('_R'):
                    primer_name = primer_name.replace('_R', '-R')
        return primer_name

    @staticmethod
    def validate_primers(primer_sequences, error_list, unidirectional=True):
        """
        Perform validations on supplied primers, such as ensuring that all primers have names and directions, that
        primers are properly paired, and do not include illegal characters
        :param primer_sequences: String of user-supplied primers to validate
        :param error_list: List of errors to present to the user
        :param unidirectional: Boolean of whether forward and reverse primers are expected. Default is True
        :return: Updated error_list
        :return: Sorted list of all primer sets
        """
        # Use StringIO to create a file-like object that can be opened by SeqIO
        fasta_io = StringIO(primer_sequences)
        # Extract all the records from the primers string
        records = SeqIO.parse(fasta_io, "fasta")
        # Initialise variable to store information parsed from the primer records
        primer_set = set()
        primer_dict = dict()
        primer_details = dict()
        primer_string = str()
        # Iterate through all the primers
        for record in records:
            # Create a variable to store the base name of the primer e.g. Lin-F is Lin
            base_primer = str()
            # Variable to store the direction of the primer e.g. Lin-F is forward
            direction = str()
            # Find the direction of the primer from the final part of the primer id
            if record.id.endswith('-F'):
                direction = 'forward'
                base_primer = record.id.split('-F')[0]
                # Clean the primer name
                base_primer = PrimerVerifierForm.clean_primer_names(primer_name=base_primer)
                # Add the base name to the set
                primer_set.add(base_primer)
            elif record.id.endswith('-R'):
                direction = 'reverse'
                base_primer = record.id.split('-R')[0]
                base_primer = PrimerVerifierForm.clean_primer_names(primer_name=base_primer)
                primer_set.add(base_primer)
            # If a primer was supplied without a -F or a -R direction, add the error to the list
            else:
                error_list.append(_('Primer {name} does not have a direction (-F or -R)'.format(name=record.id)))
            # Add the base primer name and the direction to the dictionary
            if base_primer:
                if base_primer not in primer_dict:
                    primer_dict[base_primer] = [direction]
                else:
                    primer_dict[base_primer].append(direction)
            # If there was no sequence supplied for the primer, add the error to the list
            if not record.seq:
                error_list.append(_('Primer {name} does not have any sequence data'.format(name=record.id)))
            # Initialise a dictionary to store any illegal nucleotides in the sequence
            illegal_nts = dict()
            # Iterate through all the bases in the sequence
            for iterator, nuc in enumerate(record.seq):
                # If the base isn't in the legal list of nucleotides, add the position (plus one) and the base to
                # the dictionary
                if nuc.upper() not in ['A', 'C', 'G', 'T', 'R', 'Y', 'S', 'W', 'K', 'M', 'B', 'D', 'H', 'V', 'N']:
                    illegal_nts[iterator + 1] = nuc
            # Add the error to the list
            if len(illegal_nts) == 1:
                error_list.append(_('Primer {name} has an illegal character:\n{info}'
                                    .format(name=record.id,
                                            info=illegal_nts)))
            if len(illegal_nts) > 1:
                error_list.append(_('Primer {name} has multiple illegal characters:\n{info}'
                                    .format(name=record.id,
                                            info=illegal_nts)))
            # Add the base_primer: primer_name: sequence to the primer_details
            if base_primer not in primer_details:
                primer_details[base_primer] = dict()
            # Clean the primer name
            record.id = PrimerVerifierForm.clean_primer_names(primer_name=record.id)
            # Populate the primer_name: sequence
            primer_details[base_primer][record.id] = record.seq
            # Update the primer headers in the string of the sequences
            primer_string += '>{header}\r\n{sequence}\r\n'\
                .format(header=record.id,
                        sequence=record.seq)
        # Add an error if no valid primers could be parsed
        if not primer_set:
            error_list.append(_('No valid primers supplied'))
        # Determine if one direction is missing from the supplied primers
        for base_primer, primer_list in primer_dict.items():
            for direction in ['forward', 'reverse']:
                if direction not in primer_list and unidirectional:
                    error_list.append((_('Primer set {name} is missing a {direction} primer'
                                         .format(name=base_primer,
                                                 direction=direction))))
        # Close the stream
        fasta_io.close()
        return primer_string, error_list, sorted(list(primer_set)), primer_details

    def clean(self):
        super().clean()
        # Initialise variables to store errors and primer information
        error_list = list()
        primer_list = list()
        primer_details = dict()
        # Primers
        primer_sequences = self.cleaned_data['primer_sequences']
        # Add an error if no primers were supplied
        if not primer_sequences:
            error_list.append(
                _('Please provide FASTA-formatted forward and reverse primers')
            )
        # Otherwise, validate the supplied primers
        else:
            primer_sequences, error_list, primer_list, primer_details = \
                PrimerVerifierForm.validate_primers(
                    primer_sequences=primer_sequences,
                    error_list=error_list)
        # At least one of the inclusivity/exclusivity panels must be populated
        inclusivity_panel = self.cleaned_data['inclusivity_panel']
        exclusivity_panel = self.cleaned_data['exclusivity_panel']
        if not inclusivity_panel and not exclusivity_panel:
            error_list.append(
                _('Please select at least one genus in either the inclusivity or exclusivity panel.')
            )
        # Minimum amplicon size
        min_amplicon_size = self.cleaned_data.get('min_amplicon_size')
        try:
            if min_amplicon_size < 0:
                error_list.append(
                    _('The minimum amplicon size cannot be lower than 0 bp')
                )
            if min_amplicon_size > PrimerVerifierRequest.maximum:
                error_list.append(
                    _('The minimum amplicon size cannot be greater than {max} bp'
                      .format(max=PrimerVerifierRequest.maximum))
                )
        except TypeError:
            error_list.append(
                _('Please enter an integer between 0 and {max} for the minimum amplicon size'
                  .format(max=PrimerVerifierRequest.maximum))
            )
        # Maximum amplicon size
        max_amplicon_size = self.cleaned_data.get('max_amplicon_size')
        try:
            if max_amplicon_size < 0:
                error_list.append(
                    _('The maximum amplicon size cannot be lower than 0 bp')
                )
            if max_amplicon_size > PrimerVerifierRequest.maximum:
                error_list.append(
                    _('The maximum amplicon size cannot be greater than {max} bp'
                      .format(max=PrimerVerifierRequest.maximum))
                )
        except TypeError:
            error_list.append(
                _('Please enter an integer between 0 and {max} for the maximum amplicon size'
                  .format(max=PrimerVerifierRequest.maximum))
            )
        # Range buffer
        range_buffer = self.cleaned_data.get('range_buffer')
        try:
            if range_buffer < 0:
                error_list.append(
                    _('The minimum distance between amplicons cannot be lower than 0 bp')
                )
            if range_buffer > PrimerVerifierRequest.range_maximum:
                error_list.append(
                    _('The minimum distance between amplicons cannot be greater than {max} bp'
                      .format(max=PrimerVerifierRequest.range_maximum))
                )
        except TypeError:
            error_list.append(
                _('Please enter an integer between 0 and {max} for the minimum distance between amplicons'
                  .format(max=PrimerVerifierRequest.range_maximum))
            )
        if error_list:
            raise forms.ValidationError(error_list)
        return {
            'primer_list': primer_list,
            'primer_sequences': primer_sequences,
            'primer_details': primer_details,
            'inclusivity_panel': inclusivity_panel,
            'exclusivity_panel': exclusivity_panel,
            'project_name': self.cleaned_data.get('project_name')
        }


class PrimerValidatorForm(ModelForm):
    class Meta:
        model = ValidatorRequest

        fields = [
            'project_name',
            'forward_primer',
            'reverse_primer',
            'probe_sequence',
            'inclusivity_panel',
        ]
        labels = {
            'project_name': _('Project Name'),
            'forward_primer': _('Forward Primer Sequence'),
            'reverse_primer': _('Reverse Primer Sequence'),
            'probe_sequence': _('Probe Sequence'),
            'inclusivity_panel': _('Inclusivity panel'),

        }
        widgets = {
            'project_name': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       'style': 'max-width: 18em'
                       }
            ),
            'forward_primer': forms.TextInput(
                attrs={'placeholder': _('ACGTACGT...'),
                       'style': 'max-width: 18em'
                       }
            ),
            'reverse_primer': forms.TextInput(
                attrs={'placeholder': _('ACGTACGT...'),
                       'style': 'max-width: 18em'
                       }
            ),
            'probe_sequence': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       'style': 'max-width: 18em'
                       }
            ),
            'inclusivity_panel': forms.CheckboxSelectMultiple(
                choices=genera,
            ),
        }

    def clean(self):
        super().clean()
        # Initialise variables to store errors and primer information
        error_list = list()
        primer_details = dict()
        # Primers
        forward_primer = self.cleaned_data['forward_primer']
        reverse_primer = self.cleaned_data['reverse_primer']
        forward_primer, error_list, primer_list, forward_primer_details = \
            PrimerVerifierForm.validate_primers(
                primer_sequences='>primer-F\r\n{forward}'.format(forward=forward_primer),
                error_list=error_list,
                unidirectional=False)
        reverse_primer, error_list, primer_list, reverse_primer_details = \
            PrimerVerifierForm.validate_primers(
                primer_sequences='>primer-R\r\n{reverse}'.format(reverse=reverse_primer),
                error_list=error_list,
                unidirectional=False)
        primer_details.update(forward_primer_details)
        primer_details.update(reverse_primer_details)
        # Probe
        probe_sequence = self.cleaned_data['probe_sequence'].replace(' ', '').rstrip()
        allowed_chars = 'acgt'
        if not all(char in allowed_chars for char in probe_sequence.lower()):
            error_list.append(
                _('Only the following characters are allowed in the probe sequence: A, C, G, and T')
            )
        # At least one option of the inclusivity panels must be selected
        inclusivity_panel = self.cleaned_data['inclusivity_panel']
        if not inclusivity_panel:
            error_list.append(
                _('Please select an option for the inclusivity panel.')
            )
        if error_list:
            raise forms.ValidationError(error_list)
        return {
            'forward_primer': forward_primer,
            'reverse_primer': reverse_primer,
            'primer_list': primer_list,
            'primer_details': primer_details,
            'probe_sequence': probe_sequence,
            'inclusivity_panel': inclusivity_panel,
            'project_name': self.cleaned_data.get('project_name')
        }
