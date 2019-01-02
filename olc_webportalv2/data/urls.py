from olc_webportalv2.data import views
from django.conf.urls import url, include

urlpatterns = [
    url(r'^data_home/', views.data_home, name='data_home'),
    url(r'^raw_data/', views.raw_data, name='raw_data'),
    url(r'^assembled_data/', views.assembled_data, name='assembled_data'),
    url(r'^data_download/(?P<data_request_pk>\d+)/$', views.data_download, name='data_download'),
]
