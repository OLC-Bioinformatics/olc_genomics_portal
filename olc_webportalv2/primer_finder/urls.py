# Django imports
from django.utils.translation import gettext_lazy as _
from django.conf.urls import url, include

# Portal-specific imports
from olc_webportalv2.primer_finder import views

urlpatterns = [
    url(_(r'^$'), views.primer_home, name='primer_home'),
    # PrimerVerifier
    url(_(r'^verifier/'), views.verifier_home, name='verifier_home'),
    url(_(r'^verifier_create/'), views.verifier_request, name='verifier_request'),
    url(_(r'^verifier_processing/(?P<verifier_pk>\d+)/$'), views.verifier_processing,
        name='verifier_processing'),
    url(_(r'^verifier_results/(?P<verifier_pk>\d+)/$'), views.verifier_results, name='verifier_results'),
    # PrimerValidator
    url(_(r'^validator/'), views.validator_home, name='validator_home'),
    url(_(r'^validator_create/'), views.validator_request, name='validator_request'),
    url(_(r'^validator_processing/(?P<validator_pk>\d+)/$'), views.validator_processing,
        name='validator_processing'),
    url(_(r'^validator_results/(?P<validator_pk>\d+)/$'), views.validator_results, name='validator_results'),
    url(_(r'^validator_report/(?P<validator_pk>\d+)/$'), views.validator_report, name='validator_report'),
    # PrimerFinder
    url(_(r'^finder/'), views.finder_home, name='finder_home'),
]
