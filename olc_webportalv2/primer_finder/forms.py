#!/usr/bin/env python

"""
Forms for the primer_finder app
"""

# Standard imports
from io import StringIO
import re

# Django imports
from django.utils.translation import gettext_lazy as _
from django.forms import ModelForm
from django import forms

# Third-party imports
from Bio import SeqIO
from dal import autocomplete

# Portal-specific imports
from olc_webportalv2.common.benchmarks import (
    EXCLUSIVITY_BENCHMARK_CHOICES,
    INCLUSIVITY_BENCHMARK_CHOICES,
    validate_benchmark_panel,
)
from olc_webportalv2.metadata.models import Genus, SequenceData
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorRequest
)


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


class PrimerVerifierForm(ModelForm):
    """
    Form for the PrimerVerifierRequest model.
    """

    inclusivity_genus = forms.ModelChoiceField(
        queryset=Genus.objects.all(),
        required=False,
        label=_('Genus'),
        widget=autocomplete.ModelSelect2(
            url='metadata:genus_query_autocompleter',
            attrs={'data-placeholder': _('Search genus ...')},
        ),
    )
    exclusivity_genus = forms.ModelChoiceField(
        queryset=Genus.objects.all(),
        required=False,
        label=_('Genus'),
        widget=autocomplete.ModelSelect2(
            url='metadata:genus_query_autocompleter',
            attrs={'data-placeholder': _('Search genus ...')},
        ),
    )
    inclusivity_seqids = SeqIDMultipleChoiceField(
        queryset=SequenceData.objects.all(),
        required=False,
        label=_('Specific SeqIDs'),
        widget=autocomplete.ModelSelect2Multiple(
            url='metadata:seqid_autocompleter',
            attrs={'data-placeholder': _('Search SeqIDs ...')},
        ),
    )
    exclusivity_seqids = SeqIDMultipleChoiceField(
        queryset=SequenceData.objects.all(),
        required=False,
        label=_('Specific SeqIDs'),
        widget=autocomplete.ModelSelect2Multiple(
            url='metadata:seqid_autocompleter',
            attrs={'data-placeholder': _('Search SeqIDs ...')},
        ),
    )
    inclusivity_benchmark = forms.ChoiceField(
        required=False,
        choices=(('', _('No benchmark dataset')),) +
        INCLUSIVITY_BENCHMARK_CHOICES,
        label=_('Benchmark dataset'),
    )
    exclusivity_benchmark = forms.ChoiceField(
        required=False,
        choices=(('', _('No benchmark dataset')),) +
        EXCLUSIVITY_BENCHMARK_CHOICES,
        label=_('Benchmark dataset'),
    )

    class Meta:
        """
        Meta class for the PrimerVerifierRequest form.
        """
        model = PrimerVerifierRequest
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
            "inclusivity_genus": _("Genus to include in inclusivity panel"),
            "exclusivity_genus": _("Genus to include in exclusivity panel"),
            "inclusivity_seqids": _("Specific inclusivity SeqIDs"),
            "exclusivity_seqids": _("Specific exclusivity SeqIDs"),
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
            "inclusivity_panel": forms.CheckboxSelectMultiple(
                choices=INCLUSIVITY_BENCHMARK_CHOICES,
            ),
            "exclusivity_panel": forms.CheckboxSelectMultiple(
                choices=EXCLUSIVITY_BENCHMARK_CHOICES,
            ),
        }

    inclusivity_panel = forms.MultipleChoiceField(
        required=False,
        choices=INCLUSIVITY_BENCHMARK_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    exclusivity_panel = forms.MultipleChoiceField(
        required=False,
        choices=EXCLUSIVITY_BENCHMARK_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    @staticmethod
    def clean_primer_names(primer_name):
        """
        Sanitize a primer base name (without the -F/-R suffix).
        Replace spaces/hyphens and non-word chars with underscores.
        """
        if primer_name is None:
            return ""
        name = str(primer_name).strip()
        # Replace spaces and hyphens with underscore in the base
        name = name.replace(" ", "_").replace("-", "_")
        # Collapse other disallowed characters to underscore
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)
        # Collapse multiple underscores
        name = re.sub(r"_+", "_", name).strip("_")
        return name

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
        base_clean = PrimerVerifierForm.clean_primer_names(base)
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
            # Canonicalize header up front
            canonical, error_list = PrimerVerifierForm._canonicalize_header(
                header=record.id,
                error_list=error_list
            )
            if not canonical:
                # Skip this record; error already added
                continue

            # Deduplicate headers if necessary
            if canonical in seen_headers:
                error_list.append(
                    _("Duplicate primer header {name}").format(name=canonical)
                )
                continue
            seen_headers.add(canonical)

            # Parse direction and base from canonical
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

            # Sequence validations
            if not record.seq:
                error_list.append(
                    _("Primer {name} does not have any sequence data").format(
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
                        name=canonical, info=illegal_nts
                    )
                )
            if len(illegal_nts) > 1:
                error_list.append(
                    _(
                        "Primer {name} has multiple illegal characters:"
                        "\n{info}").format(
                        name=canonical, info=illegal_nts
                    )
                )

            # Store canonical header and sequence
            if base_primer not in primer_details:
                primer_details[base_primer] = dict()
            record.id = canonical
            primer_details[base_primer][record.id] = record.seq
            primer_string += ">{header}\r\n{sequence}\r\n".format(
                header=record.id, sequence=record.seq
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
        primer_sequences = self.cleaned_data.get('primer_sequences', '')
        if not primer_sequences:
            error_list.append(
                _('Please provide FASTA-formatted forward and reverse primers')
            )
        else:
            primer_sequences, error_list, primer_list, primer_details = \
                PrimerVerifierForm.validate_primers(
                    primer_sequences=primer_sequences,
                    error_list=error_list
                )

        # Probe
        probe_sequence = \
            (self.cleaned_data.get("probe_sequence") or "").replace(
                " ", ""
            ).rstrip()
        allowed_chars = "acgt"
        if not all(char in allowed_chars for char in probe_sequence.lower()):
            error_list.append(
                _(
                    "Only the following characters are allowed in the probe "
                    "sequence: A, C, G, and T"
                )
            )

        # Inclusivity and Exclusivity Panels
        inclusivity_panel = self.cleaned_data.get(
            'inclusivity_panel', []
        ) or []
        exclusivity_panel = self.cleaned_data.get(
            'exclusivity_panel', []
        ) or []
        inclusivity_genus, inclusivity_seqids = self._clean_query_selection(
            'inclusivity'
        )
        exclusivity_genus, exclusivity_seqids = self._clean_query_selection(
            'exclusivity'
        )
        inclusivity_benchmark = self.cleaned_data.get(
            'inclusivity_benchmark', ''
        )
        exclusivity_benchmark = self.cleaned_data.get(
            'exclusivity_benchmark', ''
        )
        if not (
            inclusivity_panel or exclusivity_panel or
            inclusivity_genus or exclusivity_genus or
            inclusivity_seqids or exclusivity_seqids or
            inclusivity_benchmark or exclusivity_benchmark
        ):
            error_list.append(
                _('Select at least one benchmark, genus, or SeqID for either '
                  'panel.')
            )

        inclusivity_benchmarks = set(inclusivity_panel)
        exclusivity_benchmarks = set(exclusivity_panel)
        if inclusivity_benchmark:
            inclusivity_benchmarks.add(inclusivity_benchmark)
        if exclusivity_benchmark:
            exclusivity_benchmarks.add(exclusivity_benchmark)
        if inclusivity_benchmarks.intersection(exclusivity_benchmarks):
            error_list.append(
                _('A benchmark dataset cannot be assigned to both panels.')
            )
        if inclusivity_genus and exclusivity_genus and (
            inclusivity_genus.casefold() == exclusivity_genus.casefold()
        ):
            error_list.append(_('A genus cannot be assigned to both panels.'))
        if set(inclusivity_seqids).intersection(exclusivity_seqids):
            error_list.append(_('A SeqID cannot be assigned to both panels.'))

        for benchmark_name in inclusivity_benchmarks:
            try:
                validate_benchmark_panel(
                    benchmark_name=benchmark_name,
                    panel='inclusivity',
                )
            except ValueError as exc:
                error_list.append(str(exc))
        for benchmark_name in exclusivity_benchmarks:
            try:
                validate_benchmark_panel(
                    benchmark_name=benchmark_name,
                    panel='exclusivity',
                )
            except ValueError as exc:
                error_list.append(str(exc))

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
            "inclusivity_benchmark": inclusivity_benchmark,
            "exclusivity_benchmark": exclusivity_benchmark,
            "project_name": self.cleaned_data.get("project_name"),
        }

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
        if genus and not SequenceData.objects.filter(
            genus__iexact=genus
        ).exists():
            self.add_error(
                prefix + '_genus',
                _('This genus is not present in the sequence database.'),
            )
        return genus, sorted(set(seqids))


class PrimerValidatorForm(ModelForm):
    """
    PrimerValidatorForm class for the ValidatorRequest model.
    """
    class Meta:
        """
        Meta class for the PrimerValidatorForm.
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
                choices=INCLUSIVITY_BENCHMARK_CHOICES,
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
            PrimerVerifierForm.validate_primers(
                primer_sequences='>primer-F\r\n{forward}'.format(
                    forward=forward_primer
                ),
                error_list=error_list,
                unidirectional=False)
        reverse_primer, error_list, primer_list, reverse_primer_details = \
            PrimerVerifierForm.validate_primers(
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
    """Request-wide PrimerValidator configuration."""
    project_name = forms.CharField(
        required=False,
        max_length=256,
        help_text=_(
            'Optional: give this experiment a name you will recognize later.'
        ),
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    mismatches = forms.IntegerField(
        min_value=0, max_value=3, initial=3,
        label=_('Maximum number of mismatches'),
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )
    min_amplicon_size = forms.IntegerField(
        min_value=0,
        max_value=10000,
        initial=0,
        label=_('Minimum amplicon size'),
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )
    max_amplicon_size = forms.IntegerField(
        min_value=0,
        max_value=10000,
        initial=1500,
        label=_('Maximum amplicon size'),
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )
    range_buffer = forms.IntegerField(
        min_value=0,
        max_value=200,
        initial=0,
        label=_('Minimum distance between amplicons'),
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )
    contig_breaks = forms.BooleanField(
        required=False,
        initial=False,
        label=_('Allow searching across multiple contigs'),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    classification_rule = forms.ChoiceField(choices=(
        ('none', _('No classification rule')),
        ('salmonella', _('Salmonella: invA or stn required')),
    ), widget=forms.Select(attrs={'class': 'form-select'}))
    logic = forms.ChoiceField(
        choices=(('', _('No logic')), ('AND', _('All')), ('OR', _('Any'))),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_(
            'Applies to targets in each sample. Leave as No logic to evaluate '
            'targets independently.'
        ),
    )
    gene_exclusion_categories = forms.MultipleChoiceField(
        label=_('Sample exclusion gene-analysis categories:'),
        choices=(
            ('FRAME_SHIFT', _('Frame shift')),
            ('INTERNAL_STOP', _('Internal stop')),
            ('PARTIAL_CONTIG_END', _('Partial contig end')),
            ('EXTENDED', _('Extended gene')),
        ),
        widget=forms.CheckboxSelectMultiple(attrs={'disabled': True}),
        required=False,
        initial=(
            'FRAME_SHIFT', 'INTERNAL_STOP',
            'PARTIAL_CONTIG_END', 'EXTENDED',
        ),
    )
    inclusivity_panel = forms.MultipleChoiceField(
        choices=INCLUSIVITY_BENCHMARK_CHOICES,
        widget=forms.CheckboxSelectMultiple(),
        required=False,
    )
    exclusivity_panel = forms.MultipleChoiceField(
        choices=EXCLUSIVITY_BENCHMARK_CHOICES,
        widget=forms.CheckboxSelectMultiple(),
        required=False,
    )

    def clean(self):
        cleaned_data = super().clean()
        inclusivity = set(cleaned_data.get('inclusivity_panel') or [])
        exclusivity = set(cleaned_data.get('exclusivity_panel') or [])
        if not inclusivity and not exclusivity:
            raise forms.ValidationError(
                _('Select at least one inclusivity or exclusivity panel.')
            )
        overlap = inclusivity.intersection(exclusivity)
        if overlap:
            raise forms.ValidationError(
                _('A panel cannot be selected as both inclusivity and '
                  'exclusivity: {0}.').format(', '.join(sorted(overlap))
                                              )
            )
        minimum = cleaned_data.get('min_amplicon_size')
        maximum = cleaned_data.get('max_amplicon_size')
        if minimum is not None and maximum is not None and minimum > maximum:
            raise forms.ValidationError(
                _('Minimum amplicon size cannot exceed maximum amplicon size.')
            )
        if cleaned_data.get('classification_rule') == 'salmonella':
            cleaned_data['logic'] = ''
        return cleaned_data


class ValidatorTargetForm(forms.Form):
    """One sample target, with primer, probe, and optional gene references."""
    sample_name = forms.CharField(
        required=False, max_length=128,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Sample name'),
        }),
    )
    target_name = forms.CharField(
        max_length=62,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'invA',
        }),
    )
    forward_primer = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={
            'class': 'form-control validator-sequence-input',
            'placeholder': '5\' - 3\' primer sequence',
        }),
    )
    reverse_primer = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={
            'class': 'form-control validator-sequence-input',
            'placeholder': '5\' - 3\' primer sequence',
        }),
    )
    probe_sequence = forms.CharField(
        required=False,
        max_length=10000,
        label=_('Probe (optional)'),
        widget=forms.TextInput(attrs={
            'class': 'form-control validator-sequence-input',
            'placeholder': _('Optional probe sequence'),
        }),
    )
    probe_evaluation_method = forms.ChoiceField(
        required=False,
        choices=(
            ('percent_identity', _('Identity')),
            ('max_differences', _('Differences')),
        ),
        initial='percent_identity',
        widget=forms.RadioSelect(),
    )
    probe_percent_identity = forms.FloatField(
        required=False, initial=80.0, min_value=0.0, max_value=100.0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '0',
            'max': '100',
            'step': '0.1',
        }),
    )
    probe_max_differences = forms.IntegerField(
        required=False, initial=2, min_value=0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '0',
            'step': '1',
        }),
    )
    gene_sequence = forms.CharField(
        required=False,
        max_length=100000,
        widget=forms.Textarea(attrs={
            'class': 'form-control validator-sequence-input',
            'rows': 4,
            'placeholder': '>gene_name\nACGTACGT...',
        }),
    )
    reference_fasta = forms.FileField(
        required=False,
        label=_('Target-specific multi-FASTA upload'),
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        target_name = canonicalize_primer_base_name(
            cleaned_data.get('target_name')
        )
        if not target_name:
            self.add_error('target_name', _('Enter a valid target name.'))
        else:
            cleaned_data['target_name'] = target_name

        sample_name = canonicalize_primer_base_name(
            cleaned_data.get('sample_name') or target_name
        )
        if not sample_name:
            self.add_error('sample_name', _('Enter a valid sample name.'))
        cleaned_data['sample_name'] = sample_name

        for field_name, label in (
            ('forward_primer', _('Forward primer')),
            ('reverse_primer', _('Reverse primer')),
        ):
            sequence = _clean_validator_sequence(
                cleaned_data.get(field_name), label, self
            )
            if not sequence:
                self.add_error(field_name, _('This field is required.'))
            cleaned_data[field_name] = sequence

        probe = _clean_validator_sequence(
            cleaned_data.get('probe_sequence'), _('Probe'), self,
            required=False,
        )
        cleaned_data['probe_sequence'] = probe
        method = cleaned_data.get('probe_evaluation_method') or 'percent_identity'
        if probe and method == 'percent_identity' and (
            cleaned_data.get('probe_percent_identity') is None
        ):
            self.add_error(
                'probe_percent_identity',
                _('Enter the minimum probe percent identity.'),
            )
        if probe and method == 'max_differences' and (
            cleaned_data.get('probe_max_differences') is None
        ):
            self.add_error(
                'probe_max_differences',
                _('Enter the maximum probe differences.'),
            )

        gene_sequence = (cleaned_data.get('gene_sequence') or '').strip()
        if gene_sequence.startswith('>'):
            try:
                records = list(SeqIO.parse(StringIO(gene_sequence), 'fasta'))
            except (ValueError, TypeError):
                records = []
            if not records:
                self.add_error(
                    'gene_sequence', _('Enter a valid FASTA sequence.')
                )
            for record in records:
                sequence = str(record.seq).upper()
                if not sequence or not all(char in 'ACGTN' for char in sequence):
                    self.add_error(
                        'gene_sequence',
                        _('Gene sequences may contain only A, C, G, T, or N.'),
                    )
        elif gene_sequence:
            _clean_validator_sequence(
                gene_sequence, _('Gene sequence'), self
            )
        cleaned_data['gene_sequence'] = gene_sequence
        fasta_file = cleaned_data.get('reference_fasta')
        if fasta_file:
            _validate_gene_fasta(fasta_file, self)
        return cleaned_data


def _validate_gene_fasta(fasta_file, form):
    """Validate an uploaded target FASTA without consuming its file handle."""
    try:
        fasta_file.seek(0)
        contents = fasta_file.read()
        if isinstance(contents, bytes):
            contents = contents.decode('utf-8')
        records = list(SeqIO.parse(StringIO(contents), 'fasta'))
    except (UnicodeDecodeError, ValueError, TypeError):
        form.add_error('reference_fasta', _('Upload a valid UTF-8 FASTA file.'))
        return
    finally:
        try:
            fasta_file.seek(0)
        except (AttributeError, OSError):
            pass
    if not records:
        form.add_error('reference_fasta', _('The FASTA file contains no records.'))
        return
    identifiers = set()
    for record in records:
        sequence = str(record.seq).upper()
        if not record.id or record.id in identifiers:
            form.add_error(
                'reference_fasta',
                _('FASTA record identifiers must be unique.'),
            )
        identifiers.add(record.id)
        if not sequence or not all(char in 'ACGTN' for char in sequence):
            form.add_error(
                'reference_fasta',
                _('FASTA sequences may contain only A, C, G, T, or N.'),
            )


def _clean_validator_sequence(value, label, form, required=True):
    sequence = ''.join((value or '').split()).upper()
    if not sequence and not required:
        return ''
    if sequence and not all(char in 'ACGTN' for char in sequence):
        form.add_error(
            None,
            _('{0} may contain only A, C, G, T, or N.').format(label),
        )
    return sequence


def canonicalize_primer_base_name(value):
    name = str(value or '').strip()
    name = name.replace(' ', '_').replace('-', '_')
    name = re.sub(r'[^A-Za-z0-9_]', '_', name)
    return re.sub(r'_+', '_', name).strip('_')


class ValidatorTargetFormSet(forms.BaseFormSet):
    """Ensure target names are unique and sample-level rules are applicable."""
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen_targets = set()
        sample_targets = {}
        for form in self.forms:
            row = getattr(form, 'cleaned_data', None)
            if not row or row.get('DELETE'):
                continue
            target = row.get('target_name', '').lower()
            sample = row.get('sample_name', '').lower()
            if target in seen_targets:
                form.add_error('target_name', _('Target names must be unique.'))
            seen_targets.add(target)
            sample_targets.setdefault(sample, set()).add(target)
        classification_rule = getattr(self, 'classification_rule', '')
        if classification_rule == 'salmonella' and any(
            not {'inva', 'stn'}.intersection(targets)
            for targets in sample_targets.values()
        ):
            raise forms.ValidationError(
                _('Each Salmonella sample requires an invA or stn target.')
            )
        if getattr(self, 'logic', '') in ('AND', 'OR'):
            if any(len(targets) < 2 for targets in sample_targets.values()):
                raise forms.ValidationError(
                    _('The selected logic requires at least two targets per '
                      'sample.')
                )


ValidatorTargetFormSetFactory = forms.formset_factory(
    ValidatorTargetForm,
    formset=ValidatorTargetFormSet,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
