from django import forms
import re

from olc_webportalv2.cowbat.models import SequencingRun
from django.forms.widgets import EmailInput

from django.utils.translation import ugettext_lazy as _


class RunNameForm(forms.Form):
    run_name = forms.CharField(max_length=64, widget=forms.TextInput(attrs={'placeholder': _('YYMMDD_LAB')}), label=_('Run Name') )

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
