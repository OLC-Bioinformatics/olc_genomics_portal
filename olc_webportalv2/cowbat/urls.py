from olc_webportalv2.cowbat import views
from django.conf.urls import url, include
from django.utils.translation import gettext_lazy as _

urlpatterns = [
    url(_(r'^cowbat_processing/(?P<sequencing_run_pk>\d+)/$'), views.cowbat_processing, name='cowbat_processing'),
    url(_(r'^assembly_home/'), views.assembly_home, name='assembly_home'),
    url(_(r'^upload_metadata/'), views.upload_metadata, name='upload_metadata'),
    url(_(r'^verify_realtime/(?P<sequencing_run_pk>\d+)/$'), views.verify_realtime, name='verify_realtime'),
    url(_(r'^upload_interop/(?P<sequencing_run_pk>\d+)/$'), views.upload_interop, name='upload_interop'),
    url(_(r'^upload_sequence_data/(?P<sequencing_run_pk>\d+)/$'), views.upload_sequence_data, name='upload_sequence_data'),
    url(_(r'^retry_sequence_data_upload/(?P<sequencing_run_pk>\d+)/$'), views.retry_sequence_data_upload, name='retry_sequence_data_upload')
]
