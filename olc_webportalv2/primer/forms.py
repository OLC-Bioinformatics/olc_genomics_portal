from django.utils.translation import ugettext_lazy as _
from django.forms.formsets import BaseFormSet
from django.forms import ModelForm, FileInput
from django import forms

from .models import PrimerVal

class PrimerForm(ModelForm):

   class Meta:
        model = PrimerVal
        fields = ['name','analysistype','mismatches', 'primer_file', 'ampliconsize' , 'export_amplicons']
        labels = {
            'name': _('Name'),
            'analysistype': _('Analysis Type'),
            'mismatches': _('Mismatches'),
            'primer_file': _('Primer File'),
            'ampliconsize': _('Amplicon Size'),         
            'export_amplicons': _('Export Amplicons'),
            }
            # Limits FileInput to only be .fasta files
        widgets = {
            'primer_file':FileInput(attrs={'accept': ".fasta"}),
            }