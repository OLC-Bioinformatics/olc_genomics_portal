#!/usr/bin/env python

# Django imports
from django.utils.translation import gettext_lazy as _
from django.forms import ModelForm
from django import forms

# Third-party imports
from dal import autocomplete

# Portal-specific imports
from olc_webportalv2.cowsnphr.models import COWSNPhRRequest, ContainerName


class COWSNPhrForm(ModelForm):
    
    class Meta:
        model = COWSNPhRRequest

        fields = [
            'project_name',
            'mask_file',
            'container_name',
            'upload'
        ]

        labels = {
            'project_name': _('Project Name'),
            'mask_file': _('Mask File'),
            'container_name': _('Name of blob container with sequence files')
        }
        widgets = {
            'project_name': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       'style': 'max-width: 18em'
                       }
            ),
            'container_name': autocomplete.ModelSelect2(
           )
        }

    def clean(self):
        super().clean()
        # Initialise variables to store errors
        error_dict = {}
        container_name = self.cleaned_data['container_name']
        # Only log an error if the container_name is missing for analyses where the files are
        # already in blob storage
        if not container_name and not self.upload:
            error_dict['container_name'] = []
            error_dict['container_name'].append(
                _('Please select a blob container with sequence files')
            )
        if error_dict:
            raise forms.ValidationError(error_dict)

    def __init__(self, *args, upload=False, **kwargs):
        self.upload = upload
        super(COWSNPhrForm, self).__init__(*args, **kwargs)


class COWSNPhrSEQIDForm(ModelForm):
    
    class Meta:
        model = COWSNPhRRequest

        fields = [
            'project_name',
            'seqids',
            'ref'
        ]

        labels = {
            'project_name': _('Project Name'),
            'seqids': _('List of SEQIDs'),
            'ref': _('Reference file')
        }
        widgets = {
            'project_name': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       }
            ),
            'seqids': forms.Textarea(
                attrs={'placeholder': _('List of SEQIDs to use. One per line'),
                       }
            ),
            'ref': forms.TextInput(
                attrs={'placeholder': _('SEQID of reference file to use'),
                       }
            ),
        }

    def clean(self):
        super().clean()
        # Initialise variables to store errors
        error_dict = {}
        # Reference genome
        if not self.cleaned_data['ref']:
            if 'ref' not in error_dict:
                error_dict['ref'] = []
            error_dict['ref'].append(
                _('Please supply a reference genome SEQID')
            )
            ref = ''
        else:
            ref = self.cleaned_data['ref']
        # Queries
        try:
            seqids = [seqid.split() for seqid in self.cleaned_data['seqids']][0]
        except (KeyError, IndexError):
            if 'seqids' not in error_dict:
                error_dict['seqids'] = []
            error_dict['seqids'].append(
                _('Please supply a list of Query SEQIDs')
            )
            seqids = ['']
        project_name = self.cleaned_data['project_name']
        if error_dict:
            raise forms.ValidationError(error_dict)
        return {
            'seqids': seqids,
            'ref': ref,
            'project_name': project_name
        }

class ContainerForm(forms.Form):
    container_name = forms.ModelChoiceField(
        queryset=ContainerName.objects.all(),
        widget=autocomplete.ModelSelect2(
            url='cowsnphr:cowsnphr_autocompleter',
            attrs={
                    'data-placeholder': 'Autocomplete ...',
                    'style': 'max-width: 18em'
                }
            ),
        required=False
    )
        