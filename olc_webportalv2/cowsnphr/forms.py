#!/usr/bin/env python
"""Forms for submitting COWSNPhR analyses."""

# Django imports
# Third-party imports
from dal import autocomplete
from django import forms
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

# Portal-specific imports
from olc_webportalv2.cowsnphr.models import ContainerName, COWSNPhRRequest


class COWSNPhrForm(ModelForm):
    """Validate analysis requests using files in a blob container."""

    class Meta:
        """Configure the fields and widgets for a COWSNPhR request."""

        model = COWSNPhRRequest

        fields = ["project_name", "mask_file", "container_name", "upload"]

        labels = {
            "project_name": _("Project Name"),
            "mask_file": _("Mask File"),
            "container_name": _("Name of blob container with sequence files"),
        }
        widgets = {
            "project_name": forms.TextInput(
                attrs={
                    "placeholder": _("Optional"),
                    "style": "max-width: 18em",
                }
            ),
            "container_name": autocomplete.ModelSelect2(),
        }

    def clean(self):
        """Require a blob container when files are not being uploaded."""
        cleaned_data = super().clean()
        container_name = cleaned_data.get("container_name")
        # Only log an error for missing containers when files are already in
        # blob storage.
        if not container_name and not self.upload:
            raise forms.ValidationError(
                {
                    "container_name": [
                        _("Please select a blob container with sequence files")
                    ]
                }
            )
        return cleaned_data

    def __init__(self, *args, upload=False, **kwargs):
        """Initialize the form with its file-upload mode.

        Args:
            upload: Whether input files will be uploaded with the request.
            *args: Positional arguments passed to ``ModelForm``.
            **kwargs: Keyword arguments passed to ``ModelForm``.

        """
        self.upload = upload
        super().__init__(*args, **kwargs)


class COWSNPhrSEQIDForm(ModelForm):
    """Validate COWSNPhR requests defined by sequence identifiers."""

    class Meta:
        """Configure the fields and widgets for SEQID-based requests."""

        model = COWSNPhRRequest

        fields = ["project_name", "seqids", "ref"]

        labels = {
            "project_name": _("Project Name"),
            "seqids": _("List of SEQIDs"),
            "ref": _("Reference file"),
        }
        widgets = {
            "project_name": forms.TextInput(
                attrs={
                    "placeholder": _("Optional"),
                }
            ),
            "seqids": forms.Textarea(
                attrs={
                    "placeholder": _("List of SEQIDs to use. One per line"),
                }
            ),
            "ref": forms.TextInput(
                attrs={
                    "placeholder": _("SEQID of reference file to use"),
                }
            ),
        }

    def clean(self):
        """Validate and normalize query and reference SEQIDs."""
        cleaned_data = super().clean()
        # Initialise variables to store errors
        error_dict = {}
        # Reference genome
        if not cleaned_data.get("ref"):
            if "ref" not in error_dict:
                error_dict["ref"] = []
            error_dict["ref"].append(
                _("Please supply a reference genome SEQID")
            )
            ref = ""
        else:
            ref = cleaned_data["ref"]
        # Queries
        seqid_values = cleaned_data.get("seqids") or []
        if isinstance(seqid_values, str):
            seqid_values = [seqid_values]
        seqids = [seqid for value in seqid_values for seqid in value.split()]
        if not seqids:
            if "seqids" not in error_dict:
                error_dict["seqids"] = []
            error_dict["seqids"].append(
                _("Please supply a list of Query SEQIDs")
            )
            seqids = [""]
        project_name = cleaned_data.get("project_name", "")
        if error_dict:
            raise forms.ValidationError(error_dict)
        return {"seqids": seqids, "ref": ref, "project_name": project_name}


class ContainerForm(forms.Form):
    """Provide an autocomplete field for selecting a blob container."""

    container_name = forms.ModelChoiceField(
        queryset=ContainerName.objects.all(),
        widget=autocomplete.ModelSelect2(
            url="cowsnphr:cowsnphr_autocompleter",
            attrs={
                "data-placeholder": "Autocomplete ...",
                "style": "max-width: 18em",
            },
        ),
        required=False,
    )
