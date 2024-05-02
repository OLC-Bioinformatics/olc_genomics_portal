"""
Create URLs for AmpliSeq app
"""

# Third party imports
from django.conf.urls import url
try:
    from django.utils.translation import gettext_lazy as _
except ImportError:
    from django.utils.translation import ugettext_lazy as _
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.ampliseq import (
    tasks,
    views
)

urlpatterns = [
    url(_(r'^ampliseq/'), views.ampliseq_home, name='ampliseq'),
    url(_(r'^blob_ampliseq/'), views.blob_ampliseq,
        name='blob_ampliseq'),
    url(_(r'^upload_ampliseq/'), views.upload_ampliseq,
        name='upload_ampliseq'),
    url(_(r'^upload_ampliseq_files/(?P<ampliseq_pk>\d+)/$'),
        views.upload_ampliseq_files,
        name='upload_ampliseq_files'),
    url(_(r'^ampliseq_processing/(?P<ampliseq_pk>\d+)/$'),
        views.ampliseq_processing,
        name='ampliseq_processing'),
    url(_(r'^ampliseq_report/(?P<ampliseq_pk>\d+)/$'),
        views.ampliseq_report,
        name='ampliseq_report'),
    url(_(r'^ampliseq_timeline/(?P<ampliseq_pk>\d+)/$'),
        views.ampliseq_timeline,
        name='ampliseq_timeline'),
    # Allow user to refresh containers for autocomplete
    url(_(r'^container_refresh/'), views.container_refresh, name='refresh'),
    # Views for autocompletion
    url(r'^ampliseq_autocompleter/$',
        views.AmpliSeqAutoCompleter.as_view(),
        name='ampliseq_autocompleter'),

]

urlpatterns = format_suffix_patterns(urlpatterns)
