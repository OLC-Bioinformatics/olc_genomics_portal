"""Forms for importing metadata files."""

from django import forms
from django.utils.translation import gettext_lazy as _


class UploadFileForm(forms.Form):
    """Validate the two required CSV files and the clear-all option."""

    seqtracking_file = forms.FileField(
        label=_('SeqTracking CSV File'),
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'}),
    )
    seqmetadata_file = forms.FileField(
        label=_('SeqMetadata CSV File'),
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'}),
    )
    clear_all = forms.BooleanField(
        label=_('Clear All Existing Entries'),
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean_seqtracking_file(self):
        """Require a tracking CSV filename.

        Returns:
            The validated uploaded file.

        """
        seqtracking_file = self.cleaned_data.get('seqtracking_file')
        if 'tracking' not in seqtracking_file.name.lower():
            raise forms.ValidationError(
                _("The SeqTracking file name must contain 'tracking' "
                '(case insensitive).'
                )
            )
        return seqtracking_file

    def clean_seqmetadata_file(self):
        """Require a metadata CSV filename.

        Returns:
            The validated uploaded file.

        """
        seqmetadata_file = self.cleaned_data.get('seqmetadata_file')
        if 'metadata' not in seqmetadata_file.name.lower():
            raise forms.ValidationError(
                _("The SeqMetadata file name must contain 'metadata' "
                '(case insensitive).'
                )
            )
        return seqmetadata_file
