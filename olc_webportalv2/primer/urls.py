from django.utils.translation import gettext_lazy as _
from django.conf.urls import url, include

from olc_webportalv2.primer import views

app_name = 'primer'
urlpatterns = [
    # Primer Validator Stuff
    url(r'^$', views.primer_home, name='primer_home'),
    url(_(r'^create/'), views.primer_request, name='primer_request'),
    url(_(r'^results/(?P<primer_pk>\d+)/$'), views.primer_upload, name='primer_result'),
    url(_(r'^edit/(?P<primer_pk>\d+)/$'), views.primer_rename, name='primer_rename'),
]
