"""
URL patterns for the primer_finder app.
"""

# Django imports
from django.utils.translation import gettext_lazy as _
from django.conf.urls import url

# Portal-specific imports
from olc_webportalv2.primer_finder import views

urlpatterns = [
    url(_(r"^$"), views.primer_home, name="primer_home"),
    # PrimerVerifier
    url(
        _(r"^primer_validator/"),
        views.primer_validator_home,
        name="primer_validator_home",
    ),
    url(
        _(r"^primer_validator_create/"),
        views.primer_validator_request,
        name="primer_validator_request",
    ),
    url(
        _(r"^primer_validator_processing/(?P<validator_pk>\d+)/$"),
        views.primer_validator_processing,
        name="primer_validator_processing",
    ),
    url(
        _(r"^primer_validator_results/(?P<validator_pk>\d+)/$"),
        views.primer_validator_results,
        name="primer_validator_results",
    ),
    # PrimerValidator
    url(_(r"^primer_verifier/"),
        views.primer_verifier_home,
        name="primer_verifier_home"),
    url(
        _(r"^primer_verifier_create/"),
        views.primer_verifier_request,
        name="primer_verifier_request",
    ),
    url(
        _(r"^primer_verifier_processing/(?P<verifier_pk>\d+)/$"),
        views.primer_verifier_processing,
        name="primer_verifier_processing",
    ),
    url(
        _(r"^primer_verifier_results/(?P<verifier_pk>\d+)/$"),
        views.primer_verifier_results,
        name="primer_verifier_results",
    ),
    url(
        _(r"^primer_verifier_report/(?P<verifier_pk>\d+)/$"),
        views.primer_verifier_report,
        name="primer_verifier_report",
    ),
    # PrimerFinder
    # url(_(r'^finder/'), views.finder_home, name='finder_home'),
]
