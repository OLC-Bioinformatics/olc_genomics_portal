from django import forms
import re

from olc_webportalv2.cowbat.models import SequencingRun
from django.forms.widgets import EmailInput


class RunNameForm(forms.Form):
    run_name = forms.CharField(max_length=64)

    def clean_run_name(self):
        run_name = self.cleaned_data['run_name']
        # Cover both external lab names (123456_LAB) and olc names(123456_M01234)
        if not (re.match('\d{6}_[A-Z]+', run_name) or re.match('\d{6}_M\d+', run_name)):
            raise forms.ValidationError('Invalid run name. Format must be YYMMDD_LAB', code='BadRunName')
        return run_name

class EmailForm(forms.Form):
    email = forms.EmailField(max_length=50,label= "Email ")
