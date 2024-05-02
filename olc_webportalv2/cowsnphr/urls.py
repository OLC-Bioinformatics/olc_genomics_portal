"""
Create URLs for COWSNPhR app
"""

# Third party imports
from django.conf.urls import url
try:
    from django.utils.translation import gettext_lazy as _
except ImportError:
    from django.utils.translation import ugettext_lazy as _
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.cowsnphr import views

urlpatterns = [
    url(_(r'^cowsnphr/'), views.cowsnphr_home, name='cowsnphr'),
    url(_(r'^blob_cowsnphr/'), views.blob_cowsnphr,
        name='blob_cowsnphr'),
    url(_(r'^upload_cowsnphr/'), views.upload_cowsnphr,
        name='upload_cowsnphr'),
    url(_(r'^seqid_cowsnphr/'), views.seqid_cowsnphr,
        name='seqid_cowsnphr'),
    url(_(r'^upload_cowsnphr_files/(?P<cowsnphr_pk>\d+)/$'),
        views.upload_cowsnphr_files,
        name='upload_cowsnphr_files'),
    url(_(r'^cowsnphr_processing/(?P<cowsnphr_pk>\d+)/$'),
        views.cowsnphr_processing,
        name='cowsnphr_processing'),
    url(_(r'^cowsnphr_reports/(?P<cowsnphr_pk>\d+)/$'),
        views.cowsnphr_reports,
        name='cowsnphr_reports'),
    url(_(r'^cowsnphr_tree/(?P<cowsnphr_pk>\d+)/$'),
        views.cowsnphr_tree,
        name='cowsnphr_tree'),
    url(_(r'^cowsnphr_nucleotide_summary/(?P<cowsnphr_pk>\d+)/$'),
        views.cowsnphr_nucleotide_summary,
        name='cowsnphr_nucleotide_summary'),
    url(_(r'^cowsnphr_amino_acid_summary/(?P<cowsnphr_pk>\d+)/$'),
        views.cowsnphr_amino_acid_summary,
        name='cowsnphr_amino_acid_summary'),
    # Allow user to refresh containers for autocomplete
    url(_(r'^container_refresh/'), views.container_refresh, name='refresh'),
    # Views for autocompletion
    url(r'^cowsnphr_autocompleter/$',
        views.COWSNPhRAutoCompleter.as_view(),
        name='cowsnphr_autocompleter'),

]

urlpatterns = format_suffix_patterns(urlpatterns)
