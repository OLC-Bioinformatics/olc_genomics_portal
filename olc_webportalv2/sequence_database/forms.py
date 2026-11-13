"""Forms for Sequence Database filters and lookups."""

import re

from dal import autocomplete, forward
from django import forms
from django.forms import ModelForm
from django.forms.formsets import BaseFormSet
from django.utils.translation import gettext_lazy as _

from olc_webportalv2.sequence_database.models import (
    DatabaseQuery,
    UniqueGenus,
    UniqueMLST,
    UniqueMLSTCC,
    UniqueRMLST,
    UniqueSerovar,
    UniqueSpecies,
)

SEQID_PATTERN = re.compile(r'\d{4}-[A-Z0-9]{3,5}-\d{4}')
CFIAID_PATTERN = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,47}')


class DatabaseRequestForm(forms.Form):
    """Collect metadata filters for sequence database searches."""

    genus = forms.ModelChoiceField(
        queryset=UniqueGenus.objects.all(),
        to_field_name='genus',
        widget=autocomplete.ModelSelect2(
            url='sequence_database:genus_autocompleter',
            forward=(
                forward.Field(
                    'geneseekr',
                ),
                forward.Field(
                    'species',
                ),
                forward.Field(
                    'mlst',
                ),
                forward.Field(
                    'mlstcc',
                ),
                forward.Field(
                    'rmlst',
                ),
                forward.Field(
                    'serovar',
                ),
                forward.Field(
                    'vtyper',
                ),
            ),
        ),
        required=False,
    )
    species = forms.ModelChoiceField(
        queryset=UniqueSpecies.objects.all(),
        to_field_name='species',
        widget=autocomplete.ModelSelect2(
            url='sequence_database:species_autocompleter',
            forward=(
                forward.Field(
                    'genus',
                ),
                forward.Field(
                    'geneseekr',
                ),
                forward.Field(
                    'mlst',
                ),
                forward.Field(
                    'mlstcc',
                ),
                forward.Field(
                    'rmlst',
                ),
                forward.Field(
                    'serovar',
                ),
                forward.Field(
                    'vtyper',
                ),
            ),
        ),
        required=False,
    )

    mlst = forms.ModelChoiceField(
        queryset=UniqueMLST.objects.all(),
        to_field_name='mlst',
        widget=autocomplete.ModelSelect2(
            url='sequence_database:mlst_autocompleter',
            forward=(
                forward.Field(
                    'genus',
                ),
                forward.Field(
                    'species',
                ),
                forward.Field(
                    'geneseekr',
                ),
                forward.Field(
                    'mlstcc',
                ),
                forward.Field(
                    'rmlst',
                ),
                forward.Field(
                    'serovar',
                ),
                forward.Field(
                    'vtyper',
                ),
            ),
        ),
        required=False,
    )
    mlstcc = forms.ModelChoiceField(
        queryset=UniqueMLSTCC.objects.all(),
        to_field_name='mlst_cc',
        widget=autocomplete.ModelSelect2(
            url='sequence_database:mlstcc_autocompleter',
            forward=(
                forward.Field(
                    'genus',
                ),
                forward.Field(
                    'species',
                ),
                forward.Field(
                    'mlst',
                ),
                forward.Field(
                    'geneseekr',
                ),
                forward.Field(
                    'rmlst',
                ),
                forward.Field(
                    'serovar',
                ),
                forward.Field(
                    'vtyper',
                ),
            ),
        ),
        required=False,
    )
    rmlst = forms.ModelChoiceField(
        queryset=UniqueRMLST.objects.all(),
        to_field_name='rmlst',
        widget=autocomplete.ModelSelect2(
            url='sequence_database:rmlst_autocompleter',
            forward=(
                forward.Field(
                    'genus',
                ),
                forward.Field(
                    'species',
                ),
                forward.Field(
                    'mlst',
                ),
                forward.Field(
                    'mlstcc',
                ),
                forward.Field(
                    'geneseekr',
                ),
                forward.Field(
                    'serovar',
                ),
                forward.Field(
                    'vtyper',
                ),
            ),
        ),
        required=False,
    )
    serovar = forms.ModelChoiceField(
        queryset=UniqueSerovar.objects.all(),
        to_field_name='serovar',
        widget=autocomplete.ModelSelect2(
            url='sequence_database:serovar_autocompleter',
            forward=(
                forward.Field(
                    'genus',
                ),
                forward.Field(
                    'species',
                ),
                forward.Field(
                    'mlst',
                ),
                forward.Field(
                    'mlstcc',
                ),
                forward.Field(
                    'geneseekr',
                ),
                forward.Field(
                    'rmlst',
                ),
                forward.Field(
                    'vtyper',
                ),
            ),
        ),
        required=False,
    )
    geneseekr = forms.CharField(
        max_length=48, widget=forms.TextInput(), required=False
    )
    # serovar = forms.CharField(max_length=48, widget=forms.TextInput(),
    #                           required=False)
    vtyper = forms.CharField(
        max_length=48, widget=forms.TextInput(), required=False
    )
    start_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                'class': 'datepicker',
                'autocomplete': 'off',
            }
        ),
        required=False,
    )
    end_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                'class': 'datepicker',
                'autocomplete': 'off',
            }
        ),
        required=False,
    )

    def clean(self):
        """Return the validated filter values."""
        return super().clean()


class SequenceDatabaseBaseFormSet(BaseFormSet):
    """Validate a set of database query forms."""

    def clean(self):
        """Run the formset's default cross-form validation."""
        super().clean()


class DatabaseFieldForm(ModelForm):
    """Define one field/operator/value clause for a database query."""

    class Meta:
        model = DatabaseQuery
        fields = ['database_fields', 'query_operators', 'qualifiers', 'query']
        labels = {
            'database_fields': _('Database Fields'),
            'query_operators': _('Query Operators'),
            'qualifiers': _('Qualifiers'),
            'query': _('Query'),
        }


class DatabaseDateForm(forms.Form):
    """Collect optional lower and upper typing-date bounds."""

    start_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                'class': 'datepicker',
                'autocomplete': 'off',
            }
        ),
        required=False,
    )
    end_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                'class': 'datepicker',
                'autocomplete': 'off',
            }
        ),
        required=False,
    )


class DatabaseIDsForm(forms.Form):
    """Collect sequence IDs and CFIA IDs for an exact lookup."""

    seqids = forms.CharField(
        max_length=100000,
        widget=forms.Textarea(attrs={'placeholder': _('YYYY-LAB-####')}),
        label='',
        required=False,
    )
    cfiaids = forms.CharField(
        max_length=100000,
        widget=forms.Textarea(attrs={'placeholder': _('CFIAFB00000000')}),
        label='',
        required=False,
    )

    def clean(self):
        """Validate and combine the submitted sequence and CFIA IDs."""
        cleaned_data = super().clean()
        seqids = re.findall(
            r'[^,\s;]+', cleaned_data.get('seqids') or ''
        )
        cfiaids = re.findall(
            r'[^,\s;]+', cleaned_data.get('cfiaids') or ''
        )
        invalid_seqids = [
            seqid
            for seqid in seqids
            if not SEQID_PATTERN.fullmatch(seqid)
        ]
        invalid_cfiaids = [
            cfiaid
            for cfiaid in cfiaids
            if not CFIAID_PATTERN.fullmatch(cfiaid)
        ]

        errors = []
        if invalid_seqids:
            errors.append(
                _(
                    'Invalid SEQIDs. Expected the format '
                    'YYYY-LAB-####. Invalid values: %(ids)s'
                )
                % {'ids': ', '.join(invalid_seqids)}
            )
        if invalid_cfiaids:
            errors.append(
                _(
                    'Invalid CFIA IDs. Use letters, numbers, hyphens, or '
                    'underscores. Invalid values: %(ids)s'
                )
                % {'ids': ', '.join(invalid_cfiaids)}
            )
        if errors:
            raise forms.ValidationError(errors)

        return seqids + cfiaids
