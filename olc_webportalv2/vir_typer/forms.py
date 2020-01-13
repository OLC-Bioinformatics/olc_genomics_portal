from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from django.forms.formsets import BaseFormSet
from django.forms import ModelForm
from django import forms

from .models import VirTyperProject, VirTyperRequest


class BaseVirTyperSampleFormSet(BaseFormSet):

    def clean(self):

        super().clean()
        sample_names = list()
        lsts_ids = list()
        for form in self.forms:
            error_list = list()
            key_names = [key for key in form.cleaned_data]
            try:
                sample_name = form.cleaned_data['sample_name']
                if sample_name not in sample_names:
                    sample_names.append(sample_name)
                else:
                    error_message = _('Sample Names must be unique. The following sample names are repeated: ')
                    error_list.append(error_message + ', '.join(sample_names))
            except KeyError:
                error_list.append(form.Meta.error_messages['sample_name'])
            try:
                lsts_id = form.cleaned_data['LSTS_ID']
                if lsts_id not in lsts_ids:
                    lsts_ids.append(lsts_id)
                else:
                    error_message = _('LSTS IDs must be unique. The following LSTS IDs are repeated: ')
                    error_list.append(error_message + ', '.join(lsts_ids))
            except KeyError:
                error_list.append(form.Meta.error_messages['LSTS_ID'])
            if 'isolate_source' not in key_names:
                error_list.append(form.Meta.error_messages['isolate_source'])
            if 'analyst_name' not in key_names:
                error_list.append(form.Meta.error_messages['analyst_name'])
            if 'date_received' not in key_names:
                error_list.append(form.Meta.error_messages['date_received'])

            if error_list:
                raise forms.ValidationError(error_list)


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
                            'class': 'datepicker',
                            'autocomplete' : 'off',
                        }
                    )


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
                'unique': _('Project Name must be unique'),
                'required': _('Project Name is required'),
            },
        }


class VirTyperSampleForm(BaseModelForm):

    class Meta:
        model = VirTyperRequest
        fields = ['sample_name', 'LSTS_ID', 'lab_ID', 'putative_classification', 'isolate_source', 'analyst_name',
                  'date_received', 'subunit']
        labels = {
            'sample_name':_('Sample Name'),
            'date_received': _('Date received'),
            'LSTS_ID': _('LSTS ID'),
            'Lab_ID': _('Lab ID'),
            'subunit': _('Subunit'),
            'putative_classification': _('Putative classification'),
            'isolate_source': _('Isolate source'),
            'analyst_name': _('Analyst name')
            }
        error_messages = {
            'sample_name': {'required': _('Sample Name is required')},
            'LSTS_ID': {'required': _('LSTS ID is required')},
            'isolate_source': {'required':_('Isolate Source is required')},
            'analyst_name': {'required': _('Analyst Name is required')},
            'date_received': {'required': _('Reception Date is required')}, 
        }

class VirTyperFileForm(forms.Form):
    query_files = forms.FileField(widget=forms.ClearableFileInput(attrs={'multiple': True}), required=False, label='')

    def clean(self):
        super().clean()
        query_file_list = self.files.getlist('query_files')
        return query_file_list


class EmailForm(forms.Form):
    email = forms.EmailField(max_length=50, label="Email ")
