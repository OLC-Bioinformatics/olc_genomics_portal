"""Forms for selecting and managing FileZone containers and files."""

import re
from typing import Dict, List, Tuple

from dal import autocomplete
from django import forms
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from olc_webportalv2.common.storage_client import StorageServiceError
from olc_webportalv2.filezone.models import ContainerName, Regexes
from olc_webportalv2.filezone.tasks import find_containers


class ContainerForm(forms.Form):
    """Select an existing storage container."""

    container_name = forms.ModelChoiceField(
        queryset=ContainerName.objects.all(),
        widget=autocomplete.ModelSelect2(
            url='filezone:filezone_autocompleter',
            attrs={
                'data-placeholder': 'Autocomplete ...',
                'style': 'max-width: 18em',
            },
        ),
        required=True,
    )


class ContainerCreateForm(ModelForm):
    """Validate a new storage container name."""

    class Meta:
        """Configure the container-name form field."""

        model = ContainerName
        fields = ['container_name']
        labels = {'container_name': _('Container Name')}
        widgets = {
            'container_name': forms.TextInput(
                attrs={'style': 'max-width: 18em'}
            )
        }

    def clean(self):
        """Validate and normalize the requested container name."""
        cleaned_data = super().clean()
        container_name = cleaned_data.get('container_name')
        if not container_name:
            return cleaned_data

        sanitized_name, errors = validate_container_name(container_name)
        error_dict = {'container_name': errors}
        if not errors:
            unique_container(sanitized_name, error_dict)
        if error_dict['container_name']:
            raise forms.ValidationError(error_dict)

        cleaned_data['container_name'] = sanitized_name
        return cleaned_data


def validate_container_name(
    container_name: str,
    object_type: str = 'container',
) -> Tuple[str, List[str]]:
    """Validate a name and suggest a storage-compatible alternative.

    Args:
        container_name: User-supplied container or object name.
        object_type: Name used in validation messages.

    Returns:
        The sanitized name and any validation messages. An empty sanitized
        name is returned with errors when the input contains no valid
        characters.

    """
    errors = []
    valid_pattern = r'[a-z0-9](?!.*--)[a-z0-9-]{1,61}[a-z0-9]'
    if not re.fullmatch(valid_pattern, container_name):
        errors.append(
            _(
                '%(object_type_cap)s name, %(container_name)s is invalid. '
                '%(object_type)s names must be between 3 and 63 characters, '
                'start with a letter or number, and contain only lowercase '
                'letters, numbers, and dashes. Dashes must be surrounded by '
                'letters or numbers, and consecutive dashes are not allowed.'
            )
            % {
                'object_type_cap': object_type.capitalize(),
                'container_name': container_name,
                'object_type': object_type,
            }
        )
        original_name = container_name
        container_name = container_name.lower().replace('_', '-')
        container_name = re.sub(r'[^a-z0-9-]', '', container_name)
        container_name = re.sub(r'-{2,}', '-', container_name).strip('-')
        if container_name != original_name:
            errors.append(
                _(
                    'Attempted to fix supplied name. Please use %(name)s '
                    'instead.'
                )
                % {'name': container_name}
            )

    if not container_name:
        errors.append(
            _(
                'Fixing the %(object_type)s name left no valid characters. '
                'Please enter a new name.'
            )
            % {'object_type': object_type}
        )
        return container_name, errors

    if len(container_name) > 63:
        container_name = container_name[:63].rstrip('-')
        errors.append(
            _('%(object_type_cap)s name was too long. Use %(name)s instead.')
            % {
                'object_type_cap': object_type.capitalize(),
                'name': container_name,
            }
        )

    while len(container_name) < 3:
        longer_name = container_name + container_name
        errors.append(
            _(
                '%(object_type_cap)s name %(name)s was too short. '
                'Use %(longer)s instead.'
            )
            % {
                'object_type_cap': object_type.capitalize(),
                'name': container_name,
                'longer': longer_name,
            }
        )
        container_name = longer_name

    return container_name, errors


def unique_container(
    container_name: str,
    error_dict: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    """Add a validation error when a container already exists in storage.

    Args:
        container_name: Name to check for uniqueness.
        error_dict: Form errors keyed by field name.

    Returns:
        The supplied form error dictionary, updated if the name exists.

    """
    try:
        existing_containers = find_containers()
    except StorageServiceError as exc:
        raise forms.ValidationError(
            {
                'container_name': _(
                    'Unable to verify the container name. Please try again.'
                )
            }
        ) from exc

    if container_name in existing_containers:
        error_dict['container_name'].append(
            _(
                '%(container_name)s is already present in blob storage. '
                'Please create a unique name.'
            )
            % {'container_name': container_name}
        )
    return error_dict


class FileLocateForm(ModelForm):
    """Validate the container and file patterns used by a search."""

    class Meta:
        """Configure pattern fields and their guidance text."""

        model = Regexes
        fields = [
            'container_regex',
            'container_exclude_regex',
            'file_regex',
            'file_exclude_regex',
        ]
        labels = {
            'container_regex': _(
                'Full or Partial Container Name(s). One per line. NOTE: If '
                'supplying a partial name, include * in each term.'
            ),
            'container_exclude_regex': _(
                'Term(s) to Exclude from Container Name(s). One per line'
            ),
            'file_regex': _('Full or Partial File Name(s). One per line'),
            'file_exclude_regex': _(
                'Term(s) to Exclude from File Name(s). One per line'
            ),
        }
        widgets = {
            'container_regex': forms.Textarea(
                attrs={
                    'placeholder': _(
                        'Optional\nDefault is to search all containers\n'
                        'Only numbers, dashes, asterisks, and lowercase '
                        'letters will be used\nUnderscores and spaces become '
                        'dashes\nUppercase letters become lowercase\n'
                        'Illegal characters are ignored\nExact matches are '
                        'used when no asterisk is present\n'
                        'e.g. 201114-m05722 or 2011*m05722 or *m05722'
                    )
                }
            ),
            'container_exclude_regex': forms.Textarea(
                attrs={
                    'placeholder': _(
                        'Optional\nAll exclusions apply to all containers\n'
                        'e.g. -output or geneseekr'
                    )
                }
            ),
            'file_regex': forms.Textarea(
                attrs={
                    'placeholder': _(
                        'Required\nFull or partial file name\n'
                        'e.g. 2018-CAL-0033.fasta or 2018-CAL-*.fasta or '
                        '2018-CAL*.gz or InterOp/*.bin'
                    )
                }
            ),
            'file_exclude_regex': forms.Textarea(
                attrs={
                    'placeholder': _(
                        'Optional\nAll exclusions apply to all files\n'
                        'e.g. RawData (ignores files in a folder), .txt '
                        '(ignores text files), or unfiltered (ignores files '
                        'containing "unfiltered")'
                    )
                }
            ),
        }

    def clean(self):
        """Normalize container patterns and require a file pattern."""
        cleaned_data = super().clean()
        error_dict = {}

        container_regex = cleaned_data.get('container_regex') or '*'
        container_regex_list = (
            validate_container_regex(container_regex.split())
            if container_regex != '*'
            else ['*']
        )
        container_exclude_regex = (
            cleaned_data.get('container_exclude_regex') or ''
        )
        container_exclude_regex_list = (
            container_exclude_regex.split()
            if container_exclude_regex
            else ['']
        )

        file_regex = cleaned_data.get('file_regex') or ''
        if not file_regex:
            error_dict['file_regex'] = _(
                'Please provide at least one full or partial file name.'
            )
        file_exclude_regex = cleaned_data.get('file_exclude_regex') or ''
        file_exclude_regex_list = (
            file_exclude_regex.split() if file_exclude_regex else ['']
        )

        if error_dict:
            raise forms.ValidationError(error_dict)

        cleaned_data.update(
            {
                'container_regex': container_regex,
                'container_exclude_regex': container_exclude_regex,
                'container_regex_list': container_regex_list,
                'container_exclude_regex_list': (container_exclude_regex_list),
                'file_regex': file_regex,
                'file_exclude_regex': file_exclude_regex,
                'file_regex_list': file_regex.split(),
                'file_exclude_regex_list': file_exclude_regex_list,
            }
        )
        return cleaned_data


def validate_container_regex(
    container_regex_list: List[str],
) -> List[str]:
    """Normalize user-supplied container search patterns.

    Args:
        container_regex_list: Container patterns entered in the form.

    Returns:
        Lowercase patterns with spaces and underscores replaced by dashes
        and unsupported characters removed.

    """
    illegal_characters = set('!@#$%^&\'"?()+=[]{}\\|:;<>/,.`~')
    sanitized_regex_list = []
    for container_regex in container_regex_list:
        sanitized_regex = (
            container_regex.lower().replace(' ', '-').replace('_', '-')
        )
        for illegal_character in illegal_characters:
            sanitized_regex = sanitized_regex.replace(illegal_character, '')
        sanitized_regex_list.append(sanitized_regex)
    return sanitized_regex_list
