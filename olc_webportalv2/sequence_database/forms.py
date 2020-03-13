from django import forms
from dal import autocomplete, forward
from olc_webportalv2.sequence_database.models import Genus, Species, Serovar, MLST, MLSTCC, RMLST, SequenceData, GeneSeekr, \
    Vtyper, UniqueGenus, UniqueSpecies, UniqueMLST, UniqueMLSTCC, UniqueRMLST, UniqueGeneSeekr, UniqueSerovar, UniqueVtyper
from django.utils.translation import ugettext_lazy as _


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
    # geneseekr = forms.ModelChoiceField(MLST.objects.all(),
    #                                    widget=autocomplete.ModelSelect2(url='sequence_database:geneseekr_autocompleter',
    #                                                                     forward=(forward.Field('genus', ),
    #                                                                              forward.Field('species', ),
    #                                                                              forward.Field('mlst', ),
    #                                                                              forward.Field('mlstcc', ),
    #                                                                              forward.Field('rmlst', ),
    #                                                                              forward.Field('serovar', ),
    #                                                                              forward.Field('vtyper', )))
    #                                    )
    # serovar = forms.ModelChoiceField(MLST.objects.all(),
    #                                  widget=autocomplete.ModelSelect2(url='sequence_database:serovar_autocompleter',
    #                                                                   forward=(forward.Field('genus', ),
    #                                                                            forward.Field('species', ),
    #                                                                            forward.Field('mlst', ),
    #                                                                            forward.Field('mlstcc', ),
    #                                                                            forward.Field('rmlst', ),
    #                                                                            forward.Field('geneseekr', ),
    #                                                                            forward.Field('vtyper', )))
    #                                  )
    # vtyper = forms.ModelChoiceField(MLST.objects.all(),
    #                                 widget=autocomplete.ModelSelect2(url='sequence_database:vtyper_autocompleter',
    #                                                                  forward=(forward.Field('genus', ),
    #                                                                           forward.Field('species', ),
    #                                                                           forward.Field('mlst', ),
    #                                                                           forward.Field('mlstcc', ),
    #                                                                           forward.Field('rmlst', ),
    #                                                                           forward.Field('serovar', ),
    #                                                                           forward.Field('vtyper', )))
    #                                )
    geneseekr = forms.CharField(max_length=48, widget=forms.TextInput(),
                                required=False)
    serovar = forms.CharField(max_length=48, widget=forms.TextInput(),
                              required=False)
    vtyper = forms.CharField(max_length=48, widget=forms.TextInput(),
                             required=False)
    # seqids = forms.CharField(max_length=48, widget=forms.Textarea(),
    #                          required=False)
    # cfiaids = forms.CharField(max_length=48, widget=forms.Textarea(),
    #                           required=False)
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

    # class Meta:
    #     model = SequenceData
    #     fields = ['genus', 'species', 'mlst', 'mlstcc', 'rmlst', 'geneseekr', 'serovar', 'vtyper']
    #
    # def is_valid(self):
    #     valid = super(DatabaseRequestForm, self).is_valid()
    #
    #     if not valid:
    #         print('Errors')
    #         for key, value in vars(self).items():
    #             print(key, value)
    #     else:
    #         print('validation')
    #         for key, value in vars(self).items():
    #             print(key, value)
    #
    def clean(self):
        super().clean()
        print('spring cleaning')
        print(self.cleaned_data)
    #
    # def clean_genus(self):
    #     # super().clean()
    #     print('accio')
    #     genus = self.cleaned_data.get('genus')
    #     print('genus', genus)
    #     if genus:
    #         return str(genus)
