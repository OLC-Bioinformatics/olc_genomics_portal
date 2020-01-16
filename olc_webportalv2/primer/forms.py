from django.utils.translation import ugettext_lazy as _
from django.forms.formsets import BaseFormSet
from django.forms import ModelForm
from django import forms

from .models import PrimerVal

class PrimerForm(ModelForm):

   class Meta:
        model = PrimerVal
        fields = ['name','path', 'sequence_path', 'primer_file', 'mismatches', 'analysistype']
        labels = {
            'name': _('Name'),
            'path': _('Path'),
            'sequence_path': _('Sequence Path'),
            'primer_file': _('Primer File'),
            'mismatches': _('Mismatches'),
            'kmer_length': _('Subunit'),
            'cpus': _('CPUs'),
            'analysistype': _('Analysis Type'),
            }

