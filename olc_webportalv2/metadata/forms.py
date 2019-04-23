from django import forms

quality_choices = (
    ('Pass', 'All sequences except for low quality or contaminated sequences.'),
    ('Reference', 'Highest quality, gold standard sequences.'),
    ('Fail', 'All sequences - may include contaminated or otherwise very low quality sequences. Use with caution.')
)


class MetaDataRequestForm(forms.Form):
    # TODO: Make this an autocomplete field
    genus = forms.CharField(max_length=48, label='', required=False)
    species = forms.CharField(max_length=48, label='', required=False)
    serotype = forms.CharField(max_length=48, label='', required=False)
    mlst = forms.CharField(max_length=48, label='', required=False)
    rmlst = forms.CharField(max_length=48, label='', required=False)
    # everything_but = forms.BooleanField(required=False)
    quality = forms.ChoiceField(choices=quality_choices)


