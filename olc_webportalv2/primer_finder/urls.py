"""
URL patterns for the primer_finder app.
"""

# Django imports
from django.urls import path
from django.utils.translation import gettext_lazy as _

# Portal-specific imports
from olc_webportalv2.primer_finder import views

app_name = "primer_finder"

# URL patterns for the primer_finder app
urlpatterns = [
    path(
        "",
        views.primer_home,
        name="primer_home",
    ),

    # PrimerValidator
    path(
        _("primer_validator/"),
        views.primer_validator_home,
        name="primer_validator_home",
    ),
    path(
        _("primer_validator_create/"),
        views.primer_validator_request,
        name="primer_validator_request",
    ),
    path(
        _("primer_validator_processing/<int:validator_pk>/"),
        views.primer_validator_processing,
        name="primer_validator_processing",
    ),
    path(
        _("primer_validator_results/<int:validator_pk>/"),
        views.primer_validator_results,
        name="primer_validator_results",
    ),

    # PrimerVerifier
    path(
        _("primer_verifier/"),
        views.primer_verifier_home,
        name="primer_verifier_home",
    ),
    path(
        _("primer_verifier_create/"),
        views.primer_verifier_request,
        name="primer_verifier_request",
    ),
    path(
        _("primer_verifier_processing/<int:verifier_pk>/"),
        views.primer_verifier_processing,
        name="primer_verifier_processing",
    ),
    path(
        _("primer_verifier_results/<int:verifier_pk>/"),
        views.primer_verifier_results,
        name="primer_verifier_results",
    ),
    path(
        _("primer_verifier_report/<int:verifier_pk>/"),
        views.primer_verifier_report,
        name="primer_verifier_report",
    ),
