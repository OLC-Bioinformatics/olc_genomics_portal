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

# Third-party imports
from Bio import SeqIO

# Portal-specific imports
from olc_webportalv2.primer_finder.models import (
    genera,
    PrimerVerifierRequest,
    ValidatorRequest
)


mismatch_choices = (
    ('0', _('Zero')),
    ('1', _('One')),
    ('2', _('Two')),
    ('3', _('Three'))
)


class PrimerVerifierForm(ModelForm):
    """
    Form for the PrimerVerifierRequest model.
    """

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
            'exclusivity_panel'
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
            "inclusivity_panel": forms.CheckboxSelectMultiple(
                choices=genera,
            ),
            "exclusivity_panel": forms.CheckboxSelectMultiple(
                choices=genera,
            ),
        }

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
        primer_sequences = self.cleaned_data['primer_sequences']
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
        inclusivity_panel = self.cleaned_data['inclusivity_panel']
        exclusivity_panel = self.cleaned_data['exclusivity_panel']
        if not inclusivity_panel and not exclusivity_panel:
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
            "project_name": self.cleaned_data.get("project_name"),
        }


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
