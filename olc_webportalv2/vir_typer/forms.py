from django.utils.translation import ugettext_lazy as _
from django.forms.formsets import BaseFormSet
from django.forms import ModelForm
from django import forms

from .models import VirTyperProject, VirTyperRequest


class BaseVirTyperSampleFormSet(BaseFormSet):

    def clean(self):
        super().clean()
        for form in self.forms:
            error_list = list()
            key_names = [key for key in form.cleaned_data]
            if 'project_name' not in key_names:
                error_list.append(_('Project name is required'))
            if 'sample_name' not in key_names:
                error_list.append(_('Sample name is required'))
            if 'date_received' not in key_names:
                error_list.append(_('Reception date is required'))
            if 'LSTS_ID' not in key_names:
                error_list.append(_('LSTS ID is required'))
            if 'isolate_source' not in key_names:
                error_list.append(_('Isolate source is required'))
            if 'analyst_name' not in key_names:
                error_list.append(_('Analyst name is required'))
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
                'unique': _('The project name must be unique')
            },
        }


class VirTyperSampleForm(BaseModelForm):

    class Meta:
        model = VirTyperRequest
        fields = ['sample_name', 'LSTS_ID', 'lab_ID', 'putative_classification', 'isolate_source', 'analyst_name',
                  'date_received', 'subunit']
        labels = {
            'sample_name': _('Sample Name'),
            'date_received': _('Date received'),
            'LSTS_ID': _('LSTS ID'),
            'Lab_ID': _('Lab ID'),
            'subunit': _('Subunit'),
            'putative_classification': _('Putative classification'),
            'isolate_source': _('Isolate source'),
            'analyst_name': _('Analyst name')

        }


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
