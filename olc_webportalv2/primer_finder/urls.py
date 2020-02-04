from django.utils.translation import gettext_lazy as _
from django.conf.urls import url, include

from olc_webportalv2.primer_finder import views

app_name = 'primer_finder'
urlpatterns = [
    # Primer Validator Stuff
    url(r'^$', views.primer_home, name='primer_home'),
    url(_(r'^request/'), views.primer_request, name='primer_request'),
    url(_(r'^results/(?P<primer_request_pk>\d+)/$'), views.primer_results, name='primer_results'),
    url(_(r'^edit/(?P<primer_request_pk>\d+)/$'), views.primer_rename, name='primer_rename'),
]
