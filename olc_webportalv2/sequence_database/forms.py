from olc_webportalv2.sequence_database.models import UniqueGenus, UniqueSpecies, UniqueMLST, UniqueMLSTCC, \
    UniqueRMLST, DatabaseQuery
from django.utils.translation import ugettext_lazy as _
from django.forms.formsets import BaseFormSet
from dal import autocomplete, forward
from django.forms import ModelForm
from django import forms
import re


class DatabaseRequestForm(forms.Form):

    genus = forms.ModelChoiceField(queryset=UniqueGenus.objects.all(),
                                   to_field_name=_('genus'),
                                   widget=autocomplete.ModelSelect2(url='sequence_database:genus_autocompleter',
                                                                    forward=(forward.Field('geneseekr', ),
                                                                             forward.Field('species', ),
                                                                             forward.Field('mlst', ),
                                                                             forward.Field('mlstcc', ),
                                                                             forward.Field('rmlst', ),
                                                                             forward.Field('serovar', ),
                                                                             forward.Field('vtyper', ))),
                                   required=False
                                   )
    species = forms.ModelChoiceField(queryset=UniqueSpecies.objects.all(),
                                     to_field_name=_('species'),
                                     widget=autocomplete.ModelSelect2(
                                         url='sequence_database:species_autocompleter',
                                         forward=(forward.Field('genus', ),
                                                  forward.Field('geneseekr', ),
                                                  forward.Field('mlst', ),
                                                  forward.Field('mlstcc', ),
                                                  forward.Field('rmlst', ),
                                                  forward.Field('serovar', ),
                                                  forward.Field('vtyper', ))),
                                     required=False
                                     )

    mlst = forms.ModelChoiceField(queryset=UniqueMLST.objects.all(),
                                  to_field_name='mlst',
                                  widget=autocomplete.ModelSelect2(url='sequence_database:mlst_autocompleter',
                                                                   forward=(forward.Field('genus', ),
                                                                            forward.Field('species', ),
                                                                            forward.Field('geneseekr', ),
                                                                            forward.Field('mlstcc', ),
                                                                            forward.Field('rmlst', ),
                                                                            forward.Field('serovar', ),
                                                                            forward.Field('vtyper', ))),
                                  required=False
                                  )
    mlstcc = forms.ModelChoiceField(queryset=UniqueMLSTCC.objects.all(),
                                    to_field_name='mlst_cc',
                                    widget=autocomplete.ModelSelect2(url='sequence_database:mlstcc_autocompleter',
                                                                     forward=(forward.Field('genus', ),
                                                                              forward.Field('species', ),
                                                                              forward.Field('mlst', ),
                                                                              forward.Field('geneseekr', ),
                                                                              forward.Field('rmlst', ),
                                                                              forward.Field('serovar', ),
                                                                              forward.Field('vtyper', ))),
                                    required=False
                                    )
    rmlst = forms.ModelChoiceField(queryset=UniqueRMLST.objects.all(),
                                   to_field_name='rmlst',
                                   widget=autocomplete.ModelSelect2(url='sequence_database:rmlst_autocompleter',
                                                                    forward=(forward.Field('genus', ),
                                                                             forward.Field('species', ),
                                                                             forward.Field('mlst', ),
                                                                             forward.Field('mlstcc', ),
                                                                             forward.Field('geneseekr', ),
                                                                             forward.Field('serovar', ),
                                                                             forward.Field('vtyper', ))),
                                   required=False
                                   )
    geneseekr = forms.CharField(max_length=48, widget=forms.TextInput(),
                                required=False)
    serovar = forms.CharField(max_length=48, widget=forms.TextInput(),
                              required=False)
    vtyper = forms.CharField(max_length=48, widget=forms.TextInput(),
                             required=False)
    start_date = forms.DateField(widget=forms.DateInput(
        attrs={
            'class': 'datepicker',
            'autocomplete': 'off',
        }
    ),
        required=False)
    end_date = forms.DateField(widget=forms.DateInput(
        attrs={
            'class': 'datepicker',
            'autocomplete': 'off',
        }
    ),
        required=False)

    def clean(self):
        super().clean()


class SequenceDatabaseBaseFormSet(BaseFormSet):
    def clean(self):
        super().clean()


class DatabaseFieldForm(ModelForm):

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
    start_date = forms.DateField(widget=forms.DateInput(
        attrs={
            'class': 'datepicker',
            'autocomplete': 'off',
        }
    ),
        required=False)
    end_date = forms.DateField(widget=forms.DateInput(
        attrs={
            'class': 'datepicker',
            'autocomplete': 'off',
        }
    ),
        required=False)


class DatabaseIDsForm(forms.Form):

    seqids = forms.CharField(max_length=100000, widget=forms.Textarea(attrs={'placeholder': _('YYYY-LAB-####')}),
                             label='',
                             required=False)
    cfiaids = forms.CharField(max_length=100000, widget=forms.Textarea(attrs={'placeholder': _('CFIAFB00000000')}),
                              label='',
                              required=False)

    def clean(self):
        super().clean()
        input_ids = self.cleaned_data.get('seqids')
        # Concatenate the CFIA IDs to the end of the SEQID string
        input_ids += ',' + self.cleaned_data.get('cfiaids')
        # Split the inputs on a regex of /w (words) or - (dashes), as SEQIDs and CFIAIDs can have dashes
        id_list = re.findall(r'[\w-]+', input_ids)
        # TODO: Some validation!
        return id_list
