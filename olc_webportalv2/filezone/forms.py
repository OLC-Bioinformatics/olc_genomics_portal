# Standard imports
import re

# Django imports
from django.utils.translation import gettext_lazy as _
from django.forms import ModelForm
from django import forms

# Third-party imports
from dal import autocomplete

# Portal-specific imports
from olc_webportalv2.filezone.tasks import find_containers
from olc_webportalv2.filezone.models import (
    ContainerName,
    Regexes
)


class ContainerSelectForm(ModelForm):
    
    class Meta:
        model = ContainerName

        fields = [
            'container_name',
        ]

        labels = {
            'container_name': _('Container Name'),
        }
        widgets = {
            'container_name': autocomplete.ModelSelect2(url='filezone:container_autocompleter'
           )
        }

    def clean(self):
        super().clean()
        container_name = self.cleaned_data['container_name']
        return {
            'container_name': container_name
        }


class ContainerForm(forms.Form):
    container_name = forms.ModelChoiceField(
        queryset=ContainerName.objects.all(),
        widget=autocomplete.ModelSelect2(
            url='filezone:filezone_autocompleter',
            attrs={
                    'data-placeholder': 'Autocomplete ...',
                    'style': 'max-width: 18em'
                }
            ),
        required=False
    )

class ContainerCreateForm(ModelForm):
    
    class Meta:
        model = ContainerName

        fields = [
            'container_name',
        ]

        labels = {
            'container_name': _('Container Name'),
        }
        widgets = {
            'container_name': forms.TextInput(
                attrs={
                    'style': 'max-width: 18em'
                    }
            )
        }

    def clean(self):
        super().clean()
        error_dict = {}
        error_dict['container_name'] = []
        container_name = self.cleaned_data['container_name']
        # Ensure that the supplied container name follows the Azure container naming scheme
        sanitised_container, error_dict['container_name'] = validate_container_name(
            container_name=container_name
        )
        # Ensure that the container name is unique
        error_dict = unique_container(
            container_name=container_name,
            error_dict=error_dict
        )
        if error_dict['container_name']:
            raise forms.ValidationError(error_dict)
        return {
            'container_name': sanitised_container
        }


def validate_container_name(container_name, object_type='container'):
    """
    Use a regex to check if the supplied name follows the guidelines for Azure nomenclature. 
    If it doesn't, attempt to rename the container/object
    :param container_name: type str: Name of the container/object of interest
    :param object_type: type str: Name of the object being validated. Default is container,
    but target container, and target path are other options
    :return: container_name: String of sanitised container name
    :return: errors: List of logged statements
    """
    errors = []
    if not re.match('^[a-z0-9](?!.*--)[a-z0-9-]{1,61}[a-z0-9]$', container_name):
        errors.append(_(
            '{object_type_cap} name, {container_name} is invalid. {object_type} names must be '
            'between 3 and 63 characters, start with a letter or number, and can contain only '
            'letters, numbers, and the dash (-) character. Every dash (-) character must be ' 
            'immediately preceded and followed by a letter or number; consecutive dashes are not ' 
            'permitted in {object_type} names. All letters in a {object_type} name must be '
            'lowercase.'.format(
                object_type_cap=object_type.capitalize(),
                container_name=container_name,
                object_type=object_type
            ))
        )
        # Swap out dashes for underscores, as they will be removed in the following regex
        container_name = container_name.replace('-', '_')
        # Use re to remove all non-word characters (including dashes)
        container_name = re.sub(r'\W', '', container_name)
        # Replace multiple underscores with a single one.
        # Uses logic from: https://stackoverflow.com/a/46701355
        # Also ensure that the container name is in lowercase
        container_name = re.sub(r'[^\w\s]|(_)(?=\1)', '', container_name).lower()
        # Swap out underscores for dashes
        container_name = container_name.replace('_', '-')
        # Ensure that the container name doesn't start or end with a dash
        container_name = re.sub(r'^-+', '', container_name)
        container_name = re.sub(r'-+$', '', container_name)
        errors.append(
            _('Attempted to fix supplied container name. Please use {container_name} instead'
            .format(container_name=container_name))
        )
    # Ensure that the container name isn't length zero, or the while loop below will be infinite
    if len(container_name) == 0:
        errors.append(
            _('Attempting to fix the {object_type} name left zero valid characters! '\
            'Please enter a new name.'.format(object_type=object_type)))
        raise SystemExit
    # If the container name is too long, slice it to be 63 characters
    if len(container_name) >= 63:
        errors.append(
            _('{object_type_cap} name {container_name} was too long. '\
            'Use {container_name[:62]} instead'.format(
                object_type_cap=object_type.capitalize(),
                container_name=container_name,
                object_type=object_type)))
        container_name = container_name[:62]
    # If the container name is too short, keep adding the container name to itself to
    # bump up the length
    while len(container_name) < 3:
        errors.append(
            _('{object_type_cap} name {container_name} was too short (only {length} characters). '
            'Use {longer_name} instead'.format(
                object_type_cap=object_type.capitalize(),
                container_name=container_name,
                length=len(container_name),
                longer_name=container_name + container_name)
        ))
        container_name = container_name + container_name
    # Use the validated container name
    return container_name, errors


def unique_container(container_name, error_dict):
    """
    Ensure that the supplied container name is unique
    """
    container_set = find_containers()
    if container_name in container_set:
        error_dict['container_name'].append(
            _('{container_name} is already present in blob storage. Please create a unique name.')
            .format(container_name=container_name)
        )
    return error_dict

class FileLocateForm(ModelForm):
    
    class Meta:
        model = Regexes

        fields = [
            'container_regex',
            'container_exclude_regex',
            'file_regex',
            'file_exclude_regex'
        ]

        labels = {
            'container_regex': _('Full or Partial Container Name(s). One per line. NOTE: If supplying a partial name, you must include * somewhere in each term'),
            'container_exclude_regex': _('Term(s) to Exclude from Container Name(s). One per line'),
            'file_regex': _('Full or Partial File Name(s). One per line'),
            'file_exclude_regex': _('Term(s) to Exclude from File Name(s). One per line'),

        }
        widgets = {
            'container_regex': forms.Textarea(
                attrs={
                    'placeholder': _('Optional\nDefault is to search through all containers\n'
                    'Only numbers, dashes, asterisks, and lowercase letters will be used\n'
                    'Underscores and spaces will be converted to dashes\n'
                    'Uppercase letters will be converted to lowercase\nAll illegal characters '
                    'will be ignored\nExact matches will be used if an asterisk is not present\n'
                    'e.g. 201114-m05722 or 2011*m05722 or *m05722'),
                    }
            ),
            'container_exclude_regex': forms.Textarea(
                attrs={
                    'placeholder': _(
                        'Optional\nAll supplied exclusions will be applied to all containers\n'
                        'e.g. -output or geneseekr')
                }
            ),
            'file_regex': forms.Textarea(
                attrs={
                    'placeholder': _('Required\n'
                    'Full or partial file name\ne.g. 2018-CAL-0033.fasta or '
                    '2018-CAL-*.fasta or 2018-CAL*.gz or InterOp/*.bin'),
                    }
            ),
            'file_exclude_regex': forms.Textarea(
                attrs={
                    'placeholder': _(
                        'Optional\nAll supplied exclusions will be applied to all files\n'
                        'e.g. RawData (ignores all files in a particular folder) or .txt '
                        '(ignores all text files) or unfiltered (ignores all files containing '
                        '"unfiltered")')
                }
            ),
        }

    def clean(self):
        super().clean()
        error_dict = {}
        # Container locating terms
        if self.cleaned_data['container_regex']:
            container_regex_list = self.cleaned_data['container_regex'].split()
            sanitised_container_regex_list = validate_container_regex(
                container_regex_list=container_regex_list
            )
        else:
            self.cleaned_data['container_regex'] = '*'
            sanitised_container_regex_list = ['*']
        # Container exclusion terms
        if self.cleaned_data['container_exclude_regex']:
            container_exclude_regex_list = self.cleaned_data['container_exclude_regex'].split()
        else:
            self.cleaned_data['container_exclude_regex'] = ''
            container_exclude_regex_list = ['']
        # File locating terms
        try:
            if self.cleaned_data['file_regex']:
                file_regex_list = self.cleaned_data['file_regex'].split()
            else:
                error_dict['file_regex'] = \
                    _('Please provide at least one full or partial file name')
        except KeyError:
            error_dict['file_regex'] = _('Please provide at least one full or partial file name')
        # File exclusion terms
        if self.cleaned_data['file_exclude_regex']:
            file_exclude_regex_list = self.cleaned_data['file_exclude_regex'].split()
        else:
            self.cleaned_data['file_exclude_regex'] = ''
            file_exclude_regex_list = ['']
        if error_dict:
            raise forms.ValidationError(error_dict)
        return {
            'container_regex': self.cleaned_data['container_regex'],
            'container_exclude_regex': self.cleaned_data['container_exclude_regex'],
            'container_regex_list': sanitised_container_regex_list,
            'container_exclude_regex_list': container_exclude_regex_list,
            'file_regex': self.cleaned_data['file_regex'],
            'file_exclude_regex': self.cleaned_data['file_exclude_regex'],
            'file_regex_list': file_regex_list,
            'file_exclude_regex_list': file_exclude_regex_list,
        }


def validate_container_regex(container_regex_list: list):
    """
    Validate all entries in a user-supplied container regex
    :param list container_regex_list: List of all container regexes to use
    :return
    """
    santised_regex_list = []
    for container_regex in container_regex_list:
        santised_regex = container_regex.lower().replace(' ', '-').replace('_', '-')
        illegal_characters = ['!', '@', '#', '$', '%', '^', '&', '\'', '\"', '?', '(', ')',
                              '+', '=', '[', ']', '{', '}', '\\', '|', ':', ';', '<', '>', '/'
                              ',', '.', '`', '~']
        for illegal_character in illegal_characters:
            santised_regex = santised_regex.replace(illegal_character, '')
        santised_regex_list.append(santised_regex)
    return santised_regex_list
    