#!/usr/bin/env python

# Django imports
from django.utils.translation import gettext_lazy as _
from django.forms import ModelForm
from django import forms

# Third-party imports
from dal import autocomplete

# Portal-specific imports
from olc_webportalv2.ampliseq.models import AmpliSeqRequest, ContainerName
from olc_webportalv2.cowbat.models import ResearchRun


taxonomy_choices = (
    ('Silva 132', _('Silva 132')),
    ('Silva 138', _('Silva 138')),
    ('RDP 18', _('RDP 18')),
)

taxa_exclusion_choices = (
    ('mitochondria,chloroplast', _('mitochondria,chloroplast')),
    ('mitochondria', _('mitochondria')),
    ('chloroplast', _('chloroplast')),
    ('none', _('none'))
)

class AmpliseqForm(ModelForm):
    
    class Meta:
        model = AmpliSeqRequest

        fields = [
            'project_name',
            'forward_primer',
            'reverse_primer',
            'max_ee',
            'min_len',
            'max_len',
            'taxonomy',
            'dada_ref_taxonomy',
            'qiime_ref_taxonomy',
            'metadata',
            'classifier',
            'exclude_taxa',
            'trunc_len_f',
            'trunc_len_r',
            'container_name',
            'upload'
        ]

        labels = {
            'project_name': _('Project Name'),
            'forward_primer': _('Forward primer sequence'),
            'reverse_primer': _('Reverse primer sequence'),
            'max_ee': _('Maximum expected errors for DADA2 read filtering'),
            'min_len': _(
                'Remove reads with length less than min_len after trimming and truncation'
                ),
            'max_len': _(
                'Remove reads with length greater than max_len after trimming and truncation'
                ),
            'taxonomy': _(
                'Choose taxonomy program'),
            'dada_ref_taxonomy': _(
                'Name and version of reference database to use for DADA2 taxonomic assignment'
                ),
            'qiime_ref_taxonomy': _(
                'Name and version of reference database to use for QIIME2 taxonomic assignment'
                ),
            'metadata': _('Metadata file for sequence data files'),            
            'classifier': _('Custom classifier file for QIIME2 taxonomic assignment'),
            'exclude_taxa': _('Genus/Genera to exclude from the analyses'),
            'trunc_len_f' : _('Truncate forward reads to this length'),
            'trunc_len_r' : _('Truncate reverse reads to this length'),
            'container_name': _('Name of blob container with sequence files')
        }
        widgets = {
            'project_name': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       'style': 'max-width: 18em'
                       }
            ),
            'forward_primer': forms.TextInput(
                attrs={'placeholder': _('Required'),
                       'style': 'max-width: 30em'
                       }
            ),
            'reverse_primer': forms.TextInput(
                attrs={'placeholder': _('Required'),
                       'style': 'max-width: 30em'
                       }
            ),
            'max_ee': forms.TextInput(
                attrs={'style': 'max-width: 7em'
                       }
            ),
            'min_len': forms.TextInput(
                attrs={'style': 'max-width: 7em'
                       }
            ),
            'max_len': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       'style': 'max-width: 7em'
                       }
            ),
            'taxonomy': forms.RadioSelect(
                choices=taxonomy_choices,
                attrs={
                    'style': 'max-width: 18em',
                    'onclick': 'taxonomy_display()'
                }
            ),
            'dada_ref_taxonomy': forms.RadioSelect(
                choices=taxonomy_choices,
                attrs={
                    'style': 'max-width: 18em'
                }
            ),
            'qiime_ref_taxonomy': forms.RadioSelect(
                choices=taxonomy_choices,
                attrs={
                    'style': 'max-width: 18em'
                }
            ),
            # 'metadata': forms.FileField(),
            # 'classifier': forms.FileField(),

            # Default as both selected for exclude_taxa
            'exclude_taxa': forms.Select(
                choices=taxa_exclusion_choices,
                attrs={
                    'style': 'max-width: 18em',
                }
            ),
            'trunc_len_f': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       'style': 'max-width: 7em'
                       }
            ),
            'trunc_len_r': forms.TextInput(
                attrs={'placeholder': _('Optional'),
                       'style': 'max-width: 7em'
                       }
            ),
            'container_name': autocomplete.ModelSelect2(
                # url='ampliseq:ampliseq_autocompleter',
           )
        }

    def clean(self):
        super().clean()
        # Initialise variables to store errors and primer information
        error_dict = {}
        container_name = self.cleaned_data['container_name']
        # Only log an error if the container_name is missing for analyses where the files are
        # already in blob storage
        upload = self.cleaned_data['upload']
        if not container_name and upload:
            error_dict['container_name'] = []
            error_dict['container_name'].append(
                _('Please select a blob container with sequence files')
            )
        # Define primer variables
        forward_primer = str()
        reverse_primer = str()
        try:
            forward_primer = self.cleaned_data['forward_primer']
        except KeyError:
            pass
        try:
            reverse_primer = self.cleaned_data['reverse_primer']
        except KeyError:
            pass
        if not forward_primer and not reverse_primer:
            error_dict['primers'] = []
            error_dict['primers'].append(
                _('No primers supplied. Please supply forward and reverse primers'))
        if forward_primer and not reverse_primer:
            error_dict['reverse_primer'] = []
            error_dict['reverse_primer'].append(
                _('Only forward primer supplied. Please supply reverse primer'))
        elif not forward_primer and reverse_primer:
            error_dict['forward_primer'] = []
            error_dict['forward_primer'].append(
                _('Only reverse primer supplied. Please supply forward primer'))
        # error_list.append('no data')
        if error_dict:
            raise forms.ValidationError(error_dict)


class ContainerForm(forms.Form):
    container_name = forms.ModelChoiceField(
        queryset=ContainerName.objects.all(),
        widget=autocomplete.ModelSelect2(
            url='ampliseq:ampliseq_autocompleter',
            attrs={
                    'data-placeholder': 'Autocomplete ...',
                    'style': 'max-width: 18em'
                }
            ),
        required=False
    )
        