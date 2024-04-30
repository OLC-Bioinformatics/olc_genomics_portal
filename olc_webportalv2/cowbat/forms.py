from django.forms import ModelForm
from django import forms
import re

from olc_webportalv2.cowbat.models import SequencingRun, ResearchRun
from django.forms.widgets import EmailInput

from django.utils.translation import ugettext_lazy as _

from dal import autocomplete, forward


class RunNameForm(forms.Form):
    run_name = forms.CharField(max_length=64, widget=forms.TextInput(attrs={'placeholder': _('YYMMDD_LAB')}),
                               label=_('Run Name'))

    def clean_run_name(self):
        run_name = self.cleaned_data['run_name']
        # Cover both external lab names (123456_LAB) and olc names(123456_M01234)
        if not (re.match('\d{6}_[A-Z]+', run_name) or re.match('\d{6}_M\d+', run_name)):
            raise forms.ValidationError(_('Invalid run name. Format must be YYMMDD_LAB', code='BadRunName'))
        return run_name


def validate_no_comma(value):
    if ',' in value:
        raise forms.ValidationError(_('Strain names cannot have commas in them!'))


class RealTimeForm(forms.ModelForm):
    realtime_select = forms.MultipleChoiceField(
        help_text='',
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'checkBoxSelect'}),
        label='',
        required=False,
    )

    class Meta:
        model = SequencingRun
        fields = list()

    def __init__(self, *args, **kwargs):
        super(RealTimeForm, self).__init__(*args, **kwargs)
        choice_list = list()
        for seqid in sorted(self.instance.realtime_strains):
            choice_list.append((seqid, seqid))
        self.choice_list = tuple(choice_list)

        initials = list()
        for seqid in self.instance.realtime_strains:
            if self.instance.realtime_strains[seqid] == 'True':
                initials.append(seqid)
        self.initials = initials
        self.fields['realtime_select'].choices = self.choice_list
        self.fields['realtime_select'].initial = self.initials
        for seqid in sorted(self.instance.sample_plate):
            self.fields.update({
                seqid: forms.CharField(widget=forms.TextInput,
                                       initial=self.instance.sample_plate[seqid],
                                       required=True,
                                       validators=[validate_no_comma])
            })


class RunRequestForm(forms.Form):
    run_name = forms.ModelChoiceField(
        queryset=ResearchRun.objects.all(),
        to_field_name=_('run_name'),
        widget=autocomplete.ModelSelect2(url='cowbat:run_autocompleter', ),
        required=False
    )

    def clean(self):
        super().clean()

        try:
            self.cleaned_data['run_name'].upper().replace('-', '_')
        except (AttributeError, KeyError):
            pass


class CustomRunForm(ModelForm):
    class Meta:
        model = SequencingRun

        fields = [
            'run_name',
            'basic_assembly',
            'preprocess',
            'nextseq'
        ]
        labels = {
            'run_name': _('Run Name'),
            'basic_assembly': _('Basic Assembly'),
            'preprocess': _('Preprocess'),
            'nextseq': _('NextSeq Run')
        }

        widgets = {
            'run_name': forms.TextInput(
                attrs={'placeholder': _('YYMMDD-lab'),
                       'style': 'max-width: 18em'
                       }
            ),
        }

    def clean(self):
        super().clean()
        # Initialise variables to store errors and primer information
        error_list = list()
        run_name = str()
        try:
            run_name = self.cleaned_data['run_name']
            if not (re.match('\d{6}-[a-z]+', run_name) or re.match('\d{6}_M\d+', run_name)):
                error_list.append(
                    _('Invalid run name. Format must be YYMMDD-lab'))
            run_name.upper().replace('-', '_')
        except (AttributeError, KeyError):
            error_list.append(
                _('Please select a run to assemble'))

        basic_assembly = self.cleaned_data.get('basic_assembly')
        preprocess = self.cleaned_data.get('preprocess')
        if basic_assembly and preprocess:
            error_list.append(
                _('Basic assembly and Preprocess options are mutually exclusive. Please only select one.')
            )
        nextseq = self.cleaned_data.get('nextseq')
        if error_list:
            raise forms.ValidationError(error_list)
        return {
            'run_name': run_name.lower().replace('_', '-'),
            'basic_assembly': self.cleaned_data['basic_assembly'],
            'preprocess': self.cleaned_data['preprocess'],
            'nextseq': nextseq
        }
