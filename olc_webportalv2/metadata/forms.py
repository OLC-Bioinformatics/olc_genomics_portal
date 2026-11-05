from django import forms
from dal import autocomplete
from olc_webportalv2.metadata.models import Genus, Species, Serotype, MLST, RMLST
from django.utils.translation import gettext_lazy as _

quality_choices = (
    (_('Pass'), _('All sequences except for low quality or contaminated sequences.')),
    (_('Reference'), _('Highest quality, gold standard sequences.')),
    (_('Fail'), _('All sequences - may include contaminated or otherwise very low quality sequences. Use with caution.'))
)


def make_genus_choice_list():
    choices = Genus.objects.filter()
    choice_list = list()
    for potential_choice in choices:
        if str(potential_choice) not in choice_list:
            choice_list.append(str(potential_choice))
    return choice_list


def make_species_choice_list():
    choices = Species.objects.filter()
    choice_list = list()
    for potential_choice in choices:
        if str(potential_choice) not in choice_list:
            choice_list.append(str(potential_choice))
    return choice_list


def make_serotype_choice_list():
    choices = Serotype.objects.filter()
    choice_list = list()
    for potential_choice in choices:
        if str(potential_choice) not in choice_list:
            choice_list.append(str(potential_choice))
    return choice_list


def make_mlst_choice_list():
    choices = MLST.objects.filter()
    choice_list = list()
    for potential_choice in choices:
        if str(potential_choice) not in choice_list:
            choice_list.append(str(potential_choice))
    return choice_list


def make_rmlst_choice_list():
    choices = RMLST.objects.filter()
    choice_list = list()
    for potential_choice in choices:
        if str(potential_choice) not in choice_list:
            choice_list.append(str(potential_choice))
    return choice_list


class MetaDataRequestForm(forms.Form):
    genus = autocomplete.Select2ListChoiceField(choice_list=make_genus_choice_list,
                                                widget=autocomplete.ListSelect2(url='metadata:genus_autocompleter'),
                                                required=False)
    species = autocomplete.Select2ListChoiceField(choice_list=make_species_choice_list,
                                                  widget=autocomplete.ListSelect2(url='metadata:species_autocompleter'),
                                                  required=False)
    serotype = autocomplete.Select2ListChoiceField(choice_list=make_serotype_choice_list,
                                                   widget=autocomplete.ListSelect2(url='metadata:serotype_autocompleter'),
                                                   required=False)
    mlst = autocomplete.Select2ListChoiceField(choice_list=make_mlst_choice_list,
                                               widget=autocomplete.ListSelect2(url='metadata:mlst_autocompleter'),
                                               required=False)
    rmlst = autocomplete.Select2ListChoiceField(choice_list=make_rmlst_choice_list,
                                                widget=autocomplete.ListSelect2(url='metadata:rmlst_autocompleter'),
                                                required=False)

    quality = forms.ChoiceField(choices=quality_choices)


