from django.utils.translation import ugettext_lazy as _
from django.forms.formsets import BaseFormSet
from django.forms import ModelForm
from django import forms

from .models import PrimerVal

class PrimerForm(ModelForm):

   class Meta:
        model = PrimerVal

