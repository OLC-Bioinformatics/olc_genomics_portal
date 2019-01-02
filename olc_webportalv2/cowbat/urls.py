from olc_webportalv2.cowbat import views
from django.conf.urls import url, include

urlpatterns = [
    url(r'^cowbat_processing/(?P<sequencing_run_pk>\d+)/$', views.cowbat_processing, name='cowbat_processing'),
    url(r'^assembly_home/', views.assembly_home, name='assembly_home'),
    url(r'^upload_metadata/', views.upload_metadata, name='upload_metadata'),
    url(r'^upload_interop/(?P<sequencing_run_pk>\d+)/$', views.upload_interop, name='upload_interop'),
    url(r'^upload_sequence_data/(?P<sequencing_run_pk>\d+)/$', views.upload_sequence_data, name='upload_sequence_data'),
    url(r'^retry_sequence_data_upload/(?P<sequencing_run_pk>\d+)/$', views.retry_sequence_data_upload, name='retry_sequence_data_upload')
]
