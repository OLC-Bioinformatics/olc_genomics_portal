from django.contrib.auth.models import AbstractUser
from django.core.urlresolvers import reverse
from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _


@python_2_unicode_compatible
class User(AbstractUser):

    # First Name and Last Name do not cover name patterns
    # around the globe.
    name = models.CharField(_('Name of User'), blank=True, max_length=255)

    def __str__(self):
        s = "%s : %s : %s" % (self.username, self.rank, self.lab)
        return s

    def get_absolute_url(self):
        return reverse('users:detail', kwargs={'username': self.username})

    # Extending the user model (custom)
    rank_choices = (
        ('Diagnostic', 'Diagnostic'),
        ('Research', 'Research'),
        ('Manager', 'Manager'),
        ('Quality', 'Quality'),
        ('Super', 'Super'),
    )

    NA = 'N/A'
    STJOHNS = 'St-Johns'
    DARTMOUTH = 'Dartmouth'
    CHARLOTTETOWN = 'Charlottetown'
    STHY = 'St-Hyacinthe'
    LONGEUIL = 'Longeuil'
    FALLOWFIELD = 'Fallowfield'
    CARLING = 'Carling'
    GTA = 'Greater Toronto Area'
    WINNIPEG = 'Winnipeg'
    SASKATOON = 'Saskatoon'
    CALGARY = 'Calgary'
    LETHBRIDGE = 'Lethbridge'
    BURNABY = 'Burnaby'
    SIDNEY = 'Sidney'

    lab_choices = (
        (STJOHNS, 'St-Johns'),
        (DARTMOUTH, 'Dartmouth'),
        (CHARLOTTETOWN, 'Charlottetown'),
        (STHY, 'St-Hyacinthe'),
        (LONGEUIL, 'Longeuil'),
        (FALLOWFIELD, 'Fallowfield'),
        (CARLING, 'Carling'),
        (GTA, 'Greater Toronto Area'),
        (WINNIPEG, 'Winnipeg'),
        (SASKATOON, 'Saskatoon'),
        (CALGARY, 'Calgary'),
        (LETHBRIDGE, 'Lethbridge'),
        (BURNABY, 'Burnaby'),
        (SIDNEY, 'Sidney'),
        (NA, 'N/A')
    )

    lab = models.CharField(max_length=100, choices=lab_choices, blank=True, default=15)
    rank = models.CharField(max_length=100, choices=rank_choices, default='Diagnostic')
    cfia_access = models.BooleanField(default=False)
