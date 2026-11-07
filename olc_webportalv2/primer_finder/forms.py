#!/usr/bin/env python

"""
Forms for the primer_finder app
"""

# Standard imports
from io import StringIO
import re

# Django imports
from django.utils.translation import ugettext_lazy as _
from django.forms import ModelForm
from django import forms
from dal import autocomplete

# Third-party imports
from Bio import SeqIO

# Portal-specific imports
from olc_webportalv2.primer_finder.models import (
    genera,
    VerifierRequest,
    ValidatorRequest
)
from olc_webportalv2.common.benchmarks import (
    EXCLUSIVITY_BENCHMARK_CHOICES,
    INCLUSIVITY_BENCHMARK_CHOICES,
)
from olc_webportalv2.metadata.models import Genus, SequenceData


mismatch_choices = (
    ('0', _('Zero')),
    ('1', _('One')),
    ('2', _('Two')),
    ('3', _('Three'))
)


class SeqIDMultipleChoiceField(forms.ModelMultipleChoiceField):
    """Accept Select2 values and legacy whitespace-separated SeqIDs."""

    def clean(self, value):
        if isinstance(value, str):
            value = value.split()
        return super().clean(value)


class VerifierForm(ModelForm):
    """
    Form for the VerifierRequest model.
    """

    class Meta:
        """
        Meta class for the VerifierRequest form.
        """
        model = VerifierRequest
        fields = [
            'project_name',
            'primer_sequences',
            'probe_sequence',
            'mismatches',
            'min_amplicon_size',
            'max_amplicon_size',
            'contig_breaks',
            'range_buffer',
            'inclusivity_panel',
            'exclusivity_panel',
            'inclusivity_genus',
            'exclusivity_genus',
            'inclusivity_seqids',
            'exclusivity_seqids',
            'inclusivity_benchmark',
            'exclusivity_benchmark',
        ]
        labels = {
            "project_name": _("Project Name"),
            "primer_sequences": _("Primer Sequence(s)"),
            "probe_sequence": _("Probe Sequence"),
            "mismatches": _("Maximum Number of Mismatches"),
            "min_amplicon_size": _("Minimum Amplicon Size"),
            "max_amplicon_size": _("Maximum Amplicon Size"),
            "contig_breaks": _(
                "Allow searching for amplicons over multiple contigs?"
            ),
            "range_buffer": _("Minimum distance between amplicons"),
            "inclusivity_panel": _(
                "Genus/Genera to include in inclusivity panel"
            ),
            "exclusivity_panel": _(
                "Genus/Genera to include in exclusivity panel"
            ),
        }
        widgets = {
            "project_name": forms.TextInput(
                attrs={
                    "placeholder": _("Optional"),
                    "style": "max-width: 18em"
                }
            ),
            "primer_sequences": forms.Textarea(
                attrs={
                    "placeholder": _(
                        ">Primer_Sequence1-F \n ACGTACGT... \n "
                        ">Primer_Sequence1-R \n ACATGCC.... \n "
                        ">Primer_Sequence2-F \n ...."
                    )
                }
            ),
            "probe_sequence": forms.TextInput(
                attrs={
                    "placeholder": _("Optional"),
                    "style": "max-width: 18em"
                }
            ),
            "mismatches": forms.Select(
                choices=mismatch_choices, attrs={"style": "max-width: 18em"}
            ),
            "min_amplicon_size": forms.TextInput(
                attrs={
                    "style": "max-width: 18em"
                }
            ),
            "max_amplicon_size": forms.TextInput(
                attrs={
                    "style": "max-width: 18em"
                }
            ),
            "range_buffer": forms.TextInput(
                attrs={
                    "style": "max-width: 18em"
                }
            ),
            "inclusivity_panel": forms.CheckboxSelectMultiple(),
            "exclusivity_panel": forms.CheckboxSelectMultiple(),
        }

    inclusivity_panel = forms.MultipleChoiceField(
        required=False,
        choices=INCLUSIVITY_BENCHMARK_CHOICES,
        widget=forms.CheckboxSelectMultiple
    )

    exclusivity_panel = forms.MultipleChoiceField(
        required=False,
        choices=EXCLUSIVITY_BENCHMARK_CHOICES,
        widget=forms.CheckboxSelectMultiple
    )

    inclusivity_genus = forms.ModelChoiceField(
        queryset=Genus.objects.all(), required=False,
        label=_('Genus'),
        widget=autocomplete.ModelSelect2(
            url='geneseekr:genus_autocompleter',
            attrs={
                'data-placeholder': _('Autocomplete ...'),
                'style': 'max-width: 18em'
            }
        )
    )
    exclusivity_genus = forms.ModelChoiceField(
        queryset=Genus.objects.all(), required=False,
        label=_('Genus'),
        widget=autocomplete.ModelSelect2(
            url='geneseekr:genus_autocompleter',
            attrs={
                'data-placeholder': _('Autocomplete ...'),
                'style': 'max-width: 18em'
            }
        )
    )
    inclusivity_seqids = SeqIDMultipleChoiceField(
        queryset=SequenceData.objects.all(),
        to_field_name='seqid',
        required=False,
        widget=autocomplete.ModelSelect2Multiple(
            url='metadata:seqid_autocompleter',
            attrs={'data-placeholder': _('Search SeqIDs ...')}
        ),
        label=_('Specific SeqIDs')
    )
    exclusivity_seqids = SeqIDMultipleChoiceField(
        queryset=SequenceData.objects.all(),
        to_field_name='seqid',
        required=False,
        widget=autocomplete.ModelSelect2Multiple(
            url='metadata:seqid_autocompleter',
            attrs={'data-placeholder': _('Search SeqIDs ...')}
        ),
        label=_('Specific SeqIDs')
    )
    inclusivity_benchmark = forms.ChoiceField(
        required=False,
        choices=(('', _('No benchmark dataset')),) +
        INCLUSIVITY_BENCHMARK_CHOICES,
        label=_('Benchmark dataset')
    )

    exclusivity_benchmark = forms.ChoiceField(
        required=False,
        choices=(('', _('No benchmark dataset')),) +
        EXCLUSIVITY_BENCHMARK_CHOICES,
        label=_('Benchmark dataset')
    )

    def _clean_query_selection(self, prefix):
        genus = self.cleaned_data.get(prefix + '_genus')
        genus = genus.genus.strip() if genus else ''
        raw_seqids = self.cleaned_data.get(prefix + '_seqids')
        if hasattr(raw_seqids, 'values_list'):
            seqids = list(raw_seqids.values_list('seqid', flat=True))
        else:
            seqids = raw_seqids or []
            if isinstance(seqids, str):
                seqids = seqids.split()
        known_seqids = set(
            SequenceData.objects.filter(seqid__in=seqids).values_list(
                'seqid', flat=True
            )
        )
        invalid_seqids = sorted(set(seqids).difference(known_seqids))
        if invalid_seqids:
            self.add_error(
                prefix + '_seqids',
                _('SeqIDs not found in the database: {0}.').format(
                    ', '.join(invalid_seqids)
                )
            )
        if seqids and any(not re.match(r'^\d{4}-[A-Z]+-\d{4}$', seqid)
                          for seqid in seqids):
            self.add_error(
                prefix + '_seqids',
                _('SeqIDs must use the format YYYY-LAB-####, one per line.')
            )
        if genus and not SequenceData.objects.filter(
                genus__iexact=genus).exists():
            self.add_error(
                prefix + '_genus',
                _('This genus is not present in the sequence database.')
            )
        return genus, seqids

    @staticmethod
    def clean_primer_names(primer_name):
        """
        Sanitize a primer base name (without the -F/-R suffix).
        Replace spaces/hyphens and non-word chars with underscores.
        """
        return canonicalize_primer_base_name(primer_name)

    @staticmethod
    def _canonicalize_header(
        *,  # Enforce keyword args
        header,
        error_list
    ) -> tuple:
        """
        Canonicalize a FASTA header to Base-Dir where Dir in {F,R}.
        Accepts legacy _F/_R and normalizes to -F/-R.
        Ensures only one hyphen (the suffix separator), and a clean base.

        :param header: The FASTA header to canonicalize.
        :param error_list: List to append error messages to.
        :return: Tuple of (canonical header or None, error_list).
        """
        if header is None:
            error_list.append(_("Empty primer header"))
            return None, error_list

        raw = str(header).strip()
        # Normalize legacy suffixes
        if raw.endswith("_F"):
            raw = raw[:-2] + "-F"
        elif raw.endswith("_R"):
            raw = raw[:-2] + "-R"

        # Must end with -F or -R
        if not (raw.endswith("-F") or raw.endswith("-R")):
            error_list.append(
                _(
                    "Primer {name} must end with -F or -R (e.g., zraS1-F)"
                ).format(
                    name=header
                )
            )
            return None, error_list

        # Split once at the last hyphen (direction separator)
        try:
            base, dir_flag = raw.rsplit("-", 1)
        except ValueError:
            error_list.append(
                _(
                    "Primer {name} must contain exactly one hyphen before F/R"
                ).format(
                    name=header
                )
            )
            return None, error_list

        # Sanitize base (remove any hyphens/punctuations inside base)
        base_clean = VerifierForm.clean_primer_names(base)
        if not base_clean:
            error_list.append(
                _(
                    "Primer {name} has an empty base name before the suffix"
                ).format(
                    name=header
                )
            )
            return None, error_list

        dir_flag = dir_flag.upper()
        if dir_flag not in ("F", "R"):
            error_list.append(
                _("Primer {name} must end with -F or -R").format(name=header)
            )
            return None, error_list

        canonical = "{0}-{1}".format(base_clean, dir_flag)

        return canonical, error_list

    @staticmethod
    def validate_primers(primer_sequences, error_list, unidirectional=True):
        """
        Perform validations on supplied primers, such as ensuring that all
        primers have names and directions, that primers are properly paired,
        and do not include illegal characters.

        Args:
            primer_sequences (str): String of user-supplied primers to validate
            error_list (list): List of errors to present to the user.
            unidirectional (bool): Whether forward and reverse primers are
                expected. Default is True.

        Returns:
            tuple: Updated error_list, sorted list of all primer sets, and
                primer details.
        """
        fasta_io = StringIO(primer_sequences)
        records = SeqIO.parse(fasta_io, "fasta")
        primer_set = set()
        primer_dict = {}
        primer_details = {}
        primer_string = str()

        found_forward = False
        found_reverse = False
        seen_headers = set()

        for record in records:
            # Biopython's record.id contains only the first whitespace-delimited
            # portion of a FASTA header. Use record.description so names containing
            # spaces can be normalized rather than truncated.
            raw_header = (record.description or record.id).strip()

            canonical, error_list = VerifierForm._canonicalize_header(
                header=raw_header,
                error_list=error_list
            )
            if not canonical:
                continue

            if canonical in seen_headers:
                error_list.append(
                    _("Duplicate primer header {name}").format(
                        name=canonical
                    )
                )
                continue
            seen_headers.add(canonical)

            base_primer, dir_flag = canonical.rsplit("-", 1)
            direction = "forward" if dir_flag == "F" else "reverse"

            if direction == "forward":
                found_forward = True
            else:
                found_reverse = True

            primer_set.add(base_primer)
            if base_primer not in primer_dict:
                primer_dict[base_primer] = [direction]
            else:
                primer_dict[base_primer].append(direction)

            if not record.seq:
                error_list.append(
                    _(
                        "Primer {name} does not have any sequence data"
                    ).format(
                        name=canonical
                    )
                )

            illegal_nts = {}
            for iterator, nuc in enumerate(record.seq):
                if nuc.upper() not in [
                    "A",
                    "C",
                    "G",
                    "T",
                    "R",
                    "Y",
                    "S",
                    "W",
                    "K",
                    "M",
                    "B",
                    "D",
                    "H",
                    "V",
                    "N",
                ]:
                    illegal_nts[iterator + 1] = nuc

            if len(illegal_nts) == 1:
                error_list.append(
                    _(
                        "Primer {name} has an illegal character:\n{info}"
                    ).format(
                        name=canonical,
                        info=illegal_nts
                    )
                )
            elif len(illegal_nts) > 1:
                error_list.append(
                    _(
                        "Primer {name} has multiple illegal characters:"
                        "\n{info}"
                    ).format(
                        name=canonical,
                        info=illegal_nts
                    )
                )

            if base_primer not in primer_details:
                primer_details[base_primer] = {}

            # Store the normalized name rather than the whitespace-containing input.
            record.id = canonical
            record.name = canonical
            record.description = canonical

            primer_details[base_primer][canonical] = record.seq

            primer_string += ">{header}\r\n{sequence}\r\n".format(
                header=canonical,
                sequence=record.seq
            )

        if not primer_set:
            error_list.append(_("No valid primers supplied"))

        # Require both directions per base when unidirectional=True (default)
        for base_primer, directions in primer_dict.items():
            for direction in ["forward", "reverse"]:
                if direction not in directions and unidirectional:
                    error_list.append(
                        _(
                            "Primer set {name} is missing a {direction} primer"
                        ).format(
                            name=base_primer, direction=direction
                        )
                    )

        # Also ensure at least one F and one R overall (helps early feedback)
        if unidirectional and (not found_forward or not found_reverse):
            if not found_forward:
                error_list.append(
                    _(
                        "At least one forward primer (-F) is required"
                    )
                )
            if not found_reverse:
                error_list.append(
                    _(
                        "At least one reverse primer (-R) is required"
                    )
                )

        fasta_io.close()

        primer_set = sorted(list(primer_set))
        return primer_string, error_list, primer_set, primer_details

    MAX_AMPLICON_SIZE = 10000  # Define the maximum amplicon size
    RANGE_BUFFER = 200  # Define the range buffer

    def clean(self):
        """
        Custom validation for the form.
        """

        # Call the parent class's clean method
        super().clean()

        # Initialize error list
        error_list = []

        # Primer Validation
        primer_list = []
        primer_details = {}
        primer_sequences = self.cleaned_data['primer_sequences']
        if not primer_sequences:
            error_list.append(
                _('Please provide FASTA-formatted forward and reverse primers')
            )
        else:
            primer_sequences, error_list, primer_list, primer_details = \
                VerifierForm.validate_primers(
                    primer_sequences=primer_sequences,
                    error_list=error_list
                )

        # Probe
        probe_sequence = \
            self.cleaned_data["probe_sequence"].replace(" ", "").rstrip()
        allowed_chars = "acgt"
        if not all(char in allowed_chars for char in probe_sequence.lower()):
            error_list.append(
                _(
                    "Only the following characters are allowed in the probe "
                    "sequence: A, C, G, and T"
                )
            )

        # Inclusivity and Exclusivity Panels
        inclusivity_panel = self.cleaned_data.get('inclusivity_panel') or []
        exclusivity_panel = self.cleaned_data.get('exclusivity_panel') or []

        panel_overlap = set(inclusivity_panel).intersection(
            set(exclusivity_panel)
        )
        if panel_overlap:
            error_list.append(
                _(
                    'The following panel(s) cannot be selected for both '
                    'inclusivity and exclusivity: {0}.'
                ).format(
                    ', '.join(sorted(panel_overlap))
                )
            )

        inclusivity_genus, inclusivity_seqids = self._clean_query_selection(
            'inclusivity'
        )
        exclusivity_genus, exclusivity_seqids = self._clean_query_selection(
            'exclusivity'
        )
        if (
            not inclusivity_panel and not exclusivity_panel and
            not inclusivity_genus and not exclusivity_genus and
            not inclusivity_seqids and not exclusivity_seqids and
            not self.cleaned_data.get('inclusivity_benchmark') and
            not self.cleaned_data.get('exclusivity_benchmark')
        ):
            error_list.append(
                _('Please select at least one genus in either the inclusivity '
                  'or exclusivity panel.')
            )

        # Minimum Amplicon Size
        min_amplicon_size = self.cleaned_data.get('min_amplicon_size')
        try:
            if min_amplicon_size < 0:
                error_list.append(
                    _('The minimum amplicon size cannot be lower than 0 bp')
                )
            if min_amplicon_size > self.MAX_AMPLICON_SIZE:
                error_list.append(
                    _('The minimum amplicon size cannot be greater than {max} '
                      'bp'.format(max=self.MAX_AMPLICON_SIZE))
                )
        except TypeError:
            error_list.append(
                _('Please enter an integer between 0 and {max} for the minimum '
                  'amplicon size'.format(max=self.MAX_AMPLICON_SIZE))
            )

        # Maximum Amplicon Size
        max_amplicon_size = self.cleaned_data.get('max_amplicon_size')
        try:
            if max_amplicon_size < 0:
                error_list.append(
                    _('The maximum amplicon size cannot be lower than 0 bp')
                )
            if max_amplicon_size > self.MAX_AMPLICON_SIZE:
                error_list.append(
                    _('The maximum amplicon size cannot be greater than {max} '
                      'bp'.format(max=self.MAX_AMPLICON_SIZE))
                )
        except TypeError:
            error_list.append(
                _(
                    'Please enter an integer between 0 and {max} for the '
                    'maximum amplicon size'.format(
                        max=self.MAX_AMPLICON_SIZE
                    )
                )
            )

        # Range Buffer
        range_buffer = self.cleaned_data.get('range_buffer')
        try:
            if range_buffer < 0:
                error_list.append(
                    _(
                        'The minimum distance between amplicons cannot be '
                        'lower than 0 bp'
                    )
                )
            if range_buffer > self.RANGE_BUFFER:
                error_list.append(
                    _(
                        'The minimum distance between amplicons cannot be '
                        'greater than {max} bp'.format(
                            max=self.RANGE_BUFFER
                        )
                    )
                )
        except TypeError:
            error_list.append(
                _(
                    'Please enter an integer between 0 and {max} for the '
                    'minimum distance between amplicons'.format(
                        max=self.RANGE_BUFFER
                    )
                )
            )

        # Raise ValidationError if there are any errors
        if error_list:
            raise forms.ValidationError(error_list)
        return {
            "primer_list": primer_list,
            "primer_sequences": primer_sequences,
            "primer_details": primer_details,
            "probe_sequence": probe_sequence,
            "inclusivity_panel": inclusivity_panel,
            "exclusivity_panel": exclusivity_panel,
            "inclusivity_genus": inclusivity_genus,
            "exclusivity_genus": exclusivity_genus,
            "inclusivity_seqids": inclusivity_seqids,
            "exclusivity_seqids": exclusivity_seqids,
            "inclusivity_benchmark": self.cleaned_data.get(
                'inclusivity_benchmark'
            ),
            "exclusivity_benchmark": self.cleaned_data.get(
                'exclusivity_benchmark'
            ),
            "project_name": self.cleaned_data.get("project_name"),
        }


class ValidatorForm(ModelForm):
    """
    ValidatorForm class for the ValidatorRequest model.
    """
    class Meta:
        """
        Meta class for the ValidatorForm.
        """
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
        """
        Custom validation for the form.
        """
        super().clean()
        # Initialise variables to store errors and primer information
        error_list = list()
        primer_details = dict()
        # Primers
        forward_primer = self.cleaned_data['forward_primer']
        reverse_primer = self.cleaned_data['reverse_primer']
        forward_primer, error_list, primer_list, forward_primer_details = \
            VerifierForm.validate_primers(
                primer_sequences='>primer-F\r\n{forward}'.format(
                    forward=forward_primer
                ),
                error_list=error_list,
                unidirectional=False)
        reverse_primer, error_list, primer_list, reverse_primer_details = \
            VerifierForm.validate_primers(
                primer_sequences='>primer-R\r\n{reverse}'.format(
                    reverse=reverse_primer
                ),
                error_list=error_list,
                unidirectional=False)
        primer_details.update(forward_primer_details)
        primer_details.update(reverse_primer_details)
        # Probe
        probe_sequence = \
            self.cleaned_data['probe_sequence'].replace(' ', '').rstrip()
        allowed_chars = 'acgt'
        if not all(char in allowed_chars for char in probe_sequence.lower()):
            error_list.append(
                _(
                    'Only the following characters are allowed in the probe '
                    'sequence: A, C, G, and T'
                )
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


class ValidatorExperimentForm(forms.Form):
    """Collect request-wide locked-down Validator settings."""
    project_name = forms.CharField(required=False, max_length=256)
    mismatches = forms.IntegerField(
        min_value=0, max_value=3, initial=3,
        label='Maximum Number of Mismatches'
    )
    min_amplicon_size = forms.IntegerField(
        min_value=0, max_value=10000, initial=0,
        label='Minimum Amplicon Size'
    )
    max_amplicon_size = forms.IntegerField(
        min_value=0, max_value=10000, initial=1500,
        label='Maximum Amplicon Size'
    )
    range_buffer = forms.IntegerField(
        min_value=0, max_value=200, initial=0,
        label='Minimum distance between amplicons'
    )
    contig_breaks = forms.BooleanField(
        required=False, initial=False,
        label='Allow searching for amplicons over multiple contigs?'
    )
    classification_rule = forms.ChoiceField(choices=(
        ('none', 'No classification rule'),
        ('salmonella', 'Salmonella: invA or stn required'),
    ), help_text=(
        'Optional diagnostic rule. Salmonella classification uses only invA '
        'and stn; extra targets may be included but do not affect the '
        'classification or sample exclusions.'
    ))
    logic = forms.ChoiceField(choices=(
        ('', 'No logic'),
        ('AND', 'All'),
        ('OR', 'Any'),
    ), required=False, help_text=(
        'Optional: use All when every target is required, or Any when at '
        'least one target is required. Leave as No logic for one standalone '
        'target.'
    ))

    gene_exclusion_categories = forms.MultipleChoiceField(
        choices=(
            ('FRAME_SHIFT', 'Frame shift'),
            ('INTERNAL_STOP', 'Internal stop'),
            ('PARTIAL_CONTIG_END', 'Partial contig end'),
            ('EXTENDED', 'Extended gene'),
        ),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        initial=(
            'FRAME_SHIFT', 'INTERNAL_STOP',
            'PARTIAL_CONTIG_END', 'EXTENDED',
        ),
        label='Sample exclusion gene-analysis categories',
    )
    inclusivity_panel = forms.MultipleChoiceField(
        choices=INCLUSIVITY_BENCHMARK_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False
    )

    exclusivity_panel = forms.MultipleChoiceField(
        choices=EXCLUSIVITY_BENCHMARK_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False
    )

    def clean(self):
        cleaned_data = super().clean()
        inclusivity = set(cleaned_data.get('inclusivity_panel') or [])
        exclusivity = set(cleaned_data.get('exclusivity_panel') or [])
        if not inclusivity and not exclusivity:
            raise forms.ValidationError(
                'Select at least one inclusivity or exclusivity panel.'
            )

        overlap = inclusivity.intersection(exclusivity)
        if overlap:
            raise forms.ValidationError(
                'The following panel(s) cannot be selected for both inclusivity '
                'and exclusivity: {0}.'.format(
                    ', '.join(sorted(overlap))
                )
            )

        minimum = cleaned_data.get('min_amplicon_size')
        maximum = cleaned_data.get('max_amplicon_size')
        if (
            minimum is not None and
            maximum is not None and
            minimum > maximum
        ):
            raise forms.ValidationError(
                'The minimum amplicon size cannot be greater than the '
                'maximum amplicon size.'
            )

        if cleaned_data.get('classification_rule') not in (None, '', 'none'):
            # Classification rules define their own required-target semantics.
            cleaned_data['logic'] = ''
        return cleaned_data


class ValidatorTargetForm(forms.Form):
    """Collect one sample and diagnostic target in one form row."""
    target_name = forms.CharField(
        max_length=128, widget=forms.TextInput(attrs={
            'placeholder': 'invA'
        })
    )
    forward_primer = forms.CharField(
        required=False, max_length=10000, widget=forms.TextInput(attrs={
            'placeholder': '5\' - 3\' primer sequence'
        })
    )
    reverse_primer = forms.CharField(
        required=False, max_length=10000, widget=forms.TextInput(attrs={
            'placeholder': '5\' - 3\' primer sequence'
        })
    )
    probe_sequence = forms.CharField(
        required=False, max_length=10000, widget=forms.TextInput(attrs={
            'placeholder': 'Optional probe sequence',
            'title': (
                'When a probe is supplied, the amplicon counts as detected '
                'only when the probe also satisfies the selected criterion.'
            )
        })
    )
    probe_evaluation_method = forms.ChoiceField(
        required=False,
        choices=(
            ('percent_identity', 'Minimum percent identity'),
            ('max_differences', 'Maximum total differences'),
        ),
        initial='percent_identity',
        widget=forms.RadioSelect,
        help_text=(
            'Choose one method for deciding whether this target probe passes. '
            'Total differences are probe mismatches plus probe gaps.'
        )
    )
    probe_percent_identity = forms.FloatField(
        required=False,
        initial=80.0,
        min_value=0.0,
        max_value=100.0,
        widget=forms.NumberInput(attrs={
            'min': '0', 'max': '100', 'step': '0.1',
            'title': (
                'Minimum accepted probe percent identity. A value of 80 '
                'requires at least 80% identity.'
            )
        }),
        label='Minimum percent identity (%)'
    )
    probe_max_differences = forms.IntegerField(
        required=False,
        initial=3,
        min_value=0,
        widget=forms.NumberInput(attrs={
            'min': '0', 'step': '1',
            'title': (
                'Maximum accepted total probe differences. Total differences '
                'equal mismatches plus gaps.'
            )
        }),
        label='Maximum total differences'
    )
    gene_sequence = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'placeholder': '>gene_name\nACGTACGT...'
        }),
        max_length=100000
    )
    reference_fasta = forms.FileField(required=False)

    def clean(self):
        """Require valid diagnostic primers and optional DNA sequences."""
        cleaned_data = super().clean()
        target_name = canonicalize_primer_base_name(
            cleaned_data.get('target_name')
        )

        if not target_name:
            self.add_error(
                'target_name',
                'Enter a valid target name.'
            )
        else:
            cleaned_data['target_name'] = target_name
        forward = _clean_validator_sequence(
            cleaned_data.get('forward_primer'), 'forward primer', self
        )
        reverse = _clean_validator_sequence(
            cleaned_data.get('reverse_primer'), 'reverse primer', self
        )
        if not forward:
            self.add_error('forward_primer', 'A forward primer is required.')
        if not reverse:
            self.add_error('reverse_primer', 'A reverse primer is required.')
        probe_sequence = _clean_validator_sequence(
            cleaned_data.get('probe_sequence'), 'probe', self, required=False
        )
        cleaned_data['probe_sequence'] = probe_sequence

        method = cleaned_data.get(
            'probe_evaluation_method'
        ) or 'percent_identity'
        percent_identity = cleaned_data.get('probe_percent_identity')
        max_differences = cleaned_data.get('probe_max_differences')

        if method not in ('percent_identity', 'max_differences'):
            self.add_error(
                'probe_evaluation_method',
                'Select a valid probe evaluation method.'
            )
        elif probe_sequence and method == 'percent_identity':
            if percent_identity is None:
                self.add_error(
                    'probe_percent_identity',
                    'Enter the minimum accepted probe percent identity.'
                )
        elif probe_sequence and method == 'max_differences':
            if max_differences is None:
                self.add_error(
                    'probe_max_differences',
                    'Enter the maximum accepted number of total differences.'
                )

        gene_sequence = (cleaned_data.get('gene_sequence') or '').strip()
        if gene_sequence and '>' in gene_sequence:
            try:
                gene_records = list(SeqIO.parse(
                    StringIO(gene_sequence), 'fasta'
                ))
            except Exception:
                gene_records = []
            if not gene_records:
                self.add_error('gene_sequence', 'Enter a valid gene FASTA sequence.')
            for gene_record in gene_records:
                _clean_validator_sequence(
                    str(gene_record.seq), 'gene sequence', self, required=True
                )
        elif gene_sequence:
            _clean_validator_sequence(
                gene_sequence, 'gene sequence', self, required=False
            )
        reference_fasta = cleaned_data.get('reference_fasta')
        if reference_fasta:
            _validate_gene_fasta(reference_fasta, self)
        return cleaned_data


def _validate_gene_fasta(fasta_file, form):
    """Validate one target's uploaded gene-reference FASTA."""
    try:
        fasta_file.seek(0)
        contents = fasta_file.read()
        if isinstance(contents, bytes):
            contents = contents.decode('utf-8')
        records = list(SeqIO.parse(StringIO(contents), 'fasta'))
    except Exception:
        form.add_error('reference_fasta', 'Upload a valid UTF-8 FASTA file.')
        return
    finally:
        try:
            fasta_file.seek(0)
        except (AttributeError, IOError, OSError):
            pass
    if not records:
        form.add_error('reference_fasta', 'The FASTA file contains no records.')
        return
    identifiers = set()
    for record in records:
        if not record.id or record.id in identifiers:
            form.add_error(
                'reference_fasta',
                'FASTA record identifiers must be unique within a target.'
            )
            continue
        identifiers.add(record.id)
        sequence = str(record.seq).upper()
        if not sequence or not all(char in 'ACGTN' for char in sequence):
            form.add_error(
                'reference_fasta',
                'FASTA sequences may contain only A, C, G, T, or N.'
            )


def _clean_validator_sequence(value, label, form, required=True):
    """Normalize and validate a DNA sequence entered in the form."""
    sequence = ''.join((value or '').split()).upper()
    if not sequence and not required:
        return sequence
    if sequence and not all(char in 'ACGTN' for char in sequence):
        form.add_error(
            None,
            '{0} may contain only A, C, G, T, or N.'.format(label),
        )
    return sequence


def canonicalize_primer_base_name(value):
    """
    Normalize a primer base name for FASTA identifiers.

    The direction suffix is added separately by the caller.
    """
    name = str(value or "").strip()
    name = name.replace(" ", "_").replace("-", "_")
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name


class ValidatorTargetFormSet(forms.BaseFormSet):
    """Validate sample consistency and target uniqueness."""

    def clean(self):
        super().clean()

        if any(self.errors):
            return

        seen_targets = set()
        for form in self.forms:
            cleaned_data = getattr(form, 'cleaned_data', None)
            if not cleaned_data or cleaned_data.get('DELETE'):
                continue

            target_name = canonicalize_primer_base_name(
                cleaned_data.get('target_name')
            ).lower()

            if not target_name:
                continue

            if target_name in seen_targets:
                form.add_error('target_name', 'Target names must be unique.')
            seen_targets.add(target_name)
        if getattr(self, 'classification_rule', '') == 'salmonella':
            classification_targets = set(('inva', 'stn'))
            if not classification_targets.intersection(seen_targets):
                raise forms.ValidationError(
                    'The Salmonella classification rule requires at least '
                    'one of these targets: inva, stn.'
                )
        if (
            getattr(self, 'logic', '') in ('AND', 'OR') and
            len(seen_targets) < 2
        ):
            raise forms.ValidationError(
                'The selected logic rule requires at least two targets.'
            )


ValidatorTargetFormSetFactory = forms.formset_factory(
    ValidatorTargetForm,
    formset=ValidatorTargetFormSet,
    extra=0,
    min_num=1,
    validate_min=True,
)


# Compatibility aliases for callers using the historical form names.
PrimerVerifierForm = VerifierForm
PrimerValidatorForm = ValidatorForm
