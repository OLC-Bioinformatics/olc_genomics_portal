"""Forms for sequence data requests."""

import re

from django import forms
from django.utils.translation import gettext_lazy as _

from olc_webportalv2.metadata.models import SequenceData

SEQID_PATTERN = re.compile(r'\d{4}-[A-Z0-9]{3,5}-\d{4}')


class DataRequestForm(forms.Form):
    """Validate a list of sequence identifiers for a data request."""

    seqids = forms.CharField(
        required=False,
        max_length=2048,
        widget=forms.Textarea(attrs={'placeholder': _('YYYY-LAB-####')}),
        label='',
    )

    def clean_seqids(self):
        """Validate SEQID formatting and confirm IDs exist in the catalog."""
        seqid_input = self.cleaned_data['seqids']
        if not seqid_input:
            raise forms.ValidationError(_('Cannot be blank'))

        seqid_list = seqid_input.split()
        invalid_seqids = [
            seqid for seqid in seqid_list if not SEQID_PATTERN.fullmatch(seqid)
        ]
        if invalid_seqids:
            raise forms.ValidationError(
                _(
                    'One or more of the SEQIDs you entered was not formatted '
                    'correctly. Correct format is YYYY-LAB-####. Also, ensure '
                    'that you have entered one SEQID per line. Invalid '
                    'SEQIDs: %(seqids)s'
                )
                % {'seqids': ', '.join(invalid_seqids)}
            )

        existing_seqids = set(
            SequenceData.objects.filter(seqid__in=seqid_list).values_list(
                'seqid', flat=True
            )
        )
        missing_seqids = [
            seqid for seqid in seqid_list if seqid not in existing_seqids
        ]
        if missing_seqids:
            raise forms.ValidationError(
                _(
                    'One or more of the SEQIDs you entered was not found in '
                    'our database. SEQIDs not found: %(seqids)s'
                )
                % {'seqids': ', '.join(missing_seqids)}
            )

        return seqid_input
