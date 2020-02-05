# Django-related imports
from django.conf.urls import url, include
from django.utils.translation import gettext_lazy as _
# Data-specific code
from olc_webportalv2.data import views


urlpatterns = [
    url(_(r'^data_home/'), views.data_home, name='data_home'),
    url(_(r'^raw_data/'), views.raw_data, name='raw_data'),
    url(_(r'^assembled_data/'), views.assembled_data, name='assembled_data'),
    url(_(r'^data_download/(?P<data_request_pk>\d+)/$'), views.data_download, name='data_download'),
]
