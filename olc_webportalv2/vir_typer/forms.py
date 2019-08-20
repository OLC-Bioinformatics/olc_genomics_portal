from django import forms
from django.forms.models import formset_factory
from django.forms.formsets import BaseFormSet
from Bio import SeqIO
from io import StringIO
import re
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.geneseekr.models import GeneSeekrRequest
from django.forms.widgets import EmailInput
from django.forms import ModelForm
from .models import VirTyperProject, VirTyperRequest


class BaseVirTyperSampleFormSet(BaseFormSet):

    def clean(self):
        super().clean()
        print(self.errors)
        # raise forms.ValidationError('Missing required field!')
        # sample_names = list()
        # cleaned_data = self.cleaned_data
        # for sample in cleaned_data:
        #     try:
        #         sample_input = sample['sample_name']
        #     except KeyError:
        #         raise forms.ValidationError('Sample name required! {}'.format(cleaned_data))
        #     date_received = form.cleaned_data.get('date_received')
        #     if sample_name in sample_names:
        #         raise forms.ValidationError("Articles in a set must have distinct titles.")
        #     if not sample_name or not date_received:
        #         raise forms.ValidationError('Missing required field!')
        #     sample_names.append(sample_name)


class BaseModelForm(ModelForm):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('auto_id', '%s')
        kwargs.setdefault('label_suffix', '')
        super().__init__(*args, **kwargs)

        for field_name in self.fields:
            field = self.fields.get(field_name)
            if field:
                field.widget.attrs.update({
                    'placeholder': field.help_text,
                })
                if field_name == 'date_received':
                    field.widget = forms.TextInput(
                        attrs={
                            'placeholder': 'Reception Date',
                            'type': 'date',
                        }
                    )
                    # field.widget.attrs.update({
                    #     'placeholder': field.help_text,
                    #     'type': 'date',
                    # })
from django.utils.translation import ugettext_lazy as _
class VirTyperProjectForm(ModelForm):

    class Meta:
        model = VirTyperProject
        fields = ['project_name']
        help_texts = {
            'project_name': _('Required'),
        }
        labels = {
            'project_name': _('Project Name')
        }
        error_messages = {
            'project_name': {
                'unique': _('The project name is required')
            },
        }
        # def clean(self):
        #     super().clean()
        #     try:
        #         self.model.full_clean()
        #     except ValidationError as e:
        #         non_field_errors = e.message_dict[NON_FIELD_ERRORS]
            # validators = [UniqueValidator(queryset=VirTyperProject.objects.all())]


class VirTyperSampleForm(BaseModelForm):

    class Meta:
        model = VirTyperRequest
        fields = ['sample_name', 'LSTS_ID', 'lab_ID', 'putative_classification', 'isolate_source', 'analyst_name',
                  'date_received', 'subunit']


        # help_texts = {
        #     'sample_name': 'Format: VI####',
            # 'date_received': 'YYYY-MM-DD'
        # }



# class VirTyperSampleForm(forms.Form):
#     sample_name = forms.CharField(max_length=64, widget=forms.TextInput(attrs={'placeholder': 'Sample name'}),
#                                   label='', required=True)
#     date_received = forms.DateTimeField(required=True,
#                                         label='',
#                                         widget=forms.TextInput(
#                                             attrs={
#                                                 'placeholder': 'Reception Date',
#                                                 'type': 'date',
#                                                 }
#                                         ))


class VirTyperFileForm(forms.Form):
    query_files = forms.FileField(widget=forms.ClearableFileInput(attrs={'multiple': True}), required=False, label='')

    def clean(self):
        super().clean()
        query_file_list = self.files.getlist('query_files')
        return query_file_list


class NameForm(forms.Form):
    name = forms.CharField(label='Name ', required=False, widget=forms.TextInput(attrs={'placeholder': 'Optional'}))


class EmailForm(forms.Form):
    email = forms.EmailField(max_length=50, label="Email ")
