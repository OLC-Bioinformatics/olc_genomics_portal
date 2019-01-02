from olc_webportalv2.metadata import views
from django.conf.urls import url, include

urlpatterns = [
    url(r'^metadata_home/', views.metadata_home, name='metadata_home'),
    url(r'^metadata_results/(?P<metadata_request_pk>\d+)/$', views.metadata_results, name='metadata_results'),
]
