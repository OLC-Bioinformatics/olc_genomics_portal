"""Forms for COWBAT and research sequencing workflows."""

import re

from dal import autocomplete
from django import forms
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from olc_webportalv2.cowbat.models import ResearchRun, SequencingRun
from olc_webportalv2.cowbat.utils import normalize_research_run_name


class RunNameForm(forms.Form):
    """Validate a legacy COWBAT run name."""

    run_name = forms.CharField(
        max_length=64,
        widget=forms.TextInput(attrs={"placeholder": _("YYMMDD_LAB")}),
        label=_("Run Name"),
    )

    def clean_run_name(self):
        """Validate the run-name format.

        Returns:
            The cleaned run name.

        Raises:
            forms.ValidationError: If the name does not match a supported form.

        """
        run_name = self.cleaned_data["run_name"]
        # Accept both lab-name and OLC-style sequencing run identifiers.
        if not (
            re.match(r"\d{6}_[A-Z]+", run_name)
            or re.match(r"\d{6}_M\d+", run_name)
        ):
            raise forms.ValidationError(
                _(
                    "Invalid run name. Format must be YYMMDD_LAB",
                    code="BadRunName",
                )
            )
        return run_name


def validate_no_comma(value):
    """Reject strain names containing commas.

    Args:
        value: Strain name entered in a form field.

    Raises:
        forms.ValidationError: If the value contains a comma.

    """
    if "," in value:
        raise forms.ValidationError(
            _("Strain names cannot have commas in them!")
        )


class RealTimeForm(forms.ModelForm):
    """Configure per-strain real-time sequencing selections."""

    realtime_select = forms.MultipleChoiceField(
        help_text="",
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkBoxSelect"}),
        label="",
        required=False,
    )

    class Meta:
        """Configure the model used by the real-time selection form."""

        model = SequencingRun
        fields = list()

    def __init__(self, *args, **kwargs):
        """Build choices and plate fields from the sequencing run instance.

        Args:
            *args: Positional arguments passed to Django's model form.
            **kwargs: Keyword arguments passed to Django's model form.

        """
        super(RealTimeForm, self).__init__(*args, **kwargs)
        choice_list = list()
        for seqid in sorted(self.instance.realtime_strains):
            choice_list.append((seqid, seqid))
        self.choice_list = tuple(choice_list)

        initials = list()
        for seqid in self.instance.realtime_strains:
            if self.instance.realtime_strains[seqid] == "True":
                initials.append(seqid)
        self.initials = initials
        self.fields["realtime_select"].choices = self.choice_list
        self.fields["realtime_select"].initial = self.initials
        for seqid in sorted(self.instance.sample_plate):
            self.fields.update(
                {
                    seqid: forms.CharField(
                        widget=forms.TextInput,
                        initial=self.instance.sample_plate[seqid],
                        required=True,
                        validators=[validate_no_comma],
                    )
                }
            )


class RunRequestForm(forms.Form):
    """Select an existing research run by its run name."""

    run_name = forms.ModelChoiceField(
        queryset=ResearchRun.objects.all(),
        to_field_name="run_name",
        widget=autocomplete.ModelSelect2(
            url="cowbat:run_autocompleter",
        ),
        required=False,
    )

    def clean(self):
        """Validate the submitted run selection."""
        super().clean()

        try:
            self.cleaned_data["run_name"].upper().replace("-", "_")
        except (AttributeError, KeyError):
            pass


class CustomRunForm(ModelForm):
    """Validate and normalize a custom sequencing run request."""

    class Meta:
        """Configure model fields, labels, and widgets."""

        model = SequencingRun

        fields = ["run_name", "basic_assembly", "preprocess", "nextseq"]
        labels = {
            "run_name": _("Run Name"),
            "basic_assembly": _("Basic Assembly"),
            "preprocess": _("Preprocess"),
            "nextseq": _("NextSeq Run"),
        }

        widgets = {
            "run_name": forms.TextInput(
                attrs={
                    "placeholder": _("YYMMDD-lab"),
                    "style": "max-width: 18em",
                }
            ),
        }

    def clean_run_name(self):
        """Normalize and validate the run name.

        Returns:
            A valid normalized research run name.

        Raises:
            forms.ValidationError: If the name is not a valid container name.

        """
        try:
            return normalize_research_run_name(self.cleaned_data["run_name"])
        except ValueError as exc:
            raise forms.ValidationError(str(exc))

    def clean(self):
        """Reject conflicting assembly options.

        Returns:
            Validated form data.

        Raises:
            forms.ValidationError: If assembly and preprocessing are both set.

        """
        cleaned_data = super().clean()
        if not cleaned_data:
            return cleaned_data

        error_list = []
        basic_assembly = cleaned_data.get("basic_assembly")
        preprocess = cleaned_data.get("preprocess")
        if basic_assembly and preprocess:
            error_list.append(
                _(
                    "Basic assembly and Preprocess options are mutually "
                    "exclusive. Please only select one."
                )
            )
        if error_list:
            raise forms.ValidationError(error_list)
        return cleaned_data
