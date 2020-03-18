from django import forms
from dal import autocomplete, forward
from olc_webportalv2.sequence_database.models import Genus, Species, Serovar, MLST, MLSTCC, RMLST, SequenceData, GeneSeekr, \
    Vtyper, UniqueGenus, UniqueSpecies, UniqueMLST, UniqueMLSTCC, UniqueRMLST, UniqueGeneSeekr, UniqueSerovar, UniqueVtyper, DatabaseQuery
from django.utils.translation import ugettext_lazy as _
from django.forms.formsets import BaseFormSet
from django.forms import ModelForm

class DatabaseRequestForm(forms.Form):

    genus = forms.ModelChoiceField(queryset=UniqueGenus.objects.all(),
                                   to_field_name='genus',
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
                                     to_field_name='species',
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


# class BaseModelForm(ModelForm):
#
#     def __init__(self, *args, **kwargs):
#         kwargs.setdefault('auto_id', '%s')
#         kwargs.setdefault('label_suffix', '')
#         super().__init__(*args, **kwargs)
#         # for data in self.data:
#         #     print('data', data)
#         for field_name in self.fields:
#             field = self.fields.get(field_name)
#             if field:
#                 field.widget.attrs.update({
#                     'placeholder': field.help_text,
#                 })
#                 if field_name == 'query':
#                     # print(self.fields['database_fields'])
#                     # print('field', dir(field))
#                     # for key, value in vars(self.fields['database_fields']).items():
#                     #     print(key, value)
#                     field.widget = forms.TextInput(
#                         attrs={
#                             'class': 'datepicker',
#                             'autocomplete': 'off',
#                         }
#                     )

class DatabaseFieldForm(ModelForm):

    class Meta:
        model = DatabaseQuery
        fields = ['database_fields', 'query_operators', 'qualifiers', 'query']


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
