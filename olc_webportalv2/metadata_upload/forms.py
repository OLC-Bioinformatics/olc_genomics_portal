from django import forms


class UploadFileForm(forms.Form):
    seqtracking_file = forms.FileField(
        label='SeqTracking CSV File',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'})
    )
    seqmetadata_file = forms.FileField(
        label='SeqMetadata CSV File',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'})
    )
    clear_all = forms.BooleanField(
        label='Clear All Existing Entries',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def clean_seqtracking_file(self):
        seqtracking_file = self.cleaned_data.get('seqtracking_file')
        if 'tracking' not in seqtracking_file.name.lower():
            raise forms.ValidationError(
                "The SeqTracking file must contain the word 'tracking' (case insensitive).")
        return seqtracking_file

    def clean_seqmetadata_file(self):
        seqmetadata_file = self.cleaned_data.get('seqmetadata_file')
        if 'metadata' not in seqmetadata_file.name.lower():
            raise forms.ValidationError(
                "The SeqMetadata file must contain the word 'metadata' (case sensitive).")
        return seqmetadata_file
