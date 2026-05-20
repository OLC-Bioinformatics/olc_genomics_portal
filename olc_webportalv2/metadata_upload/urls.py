from django.conf.urls import url
from django.utils.translation import gettext_lazy as _

from olc_webportalv2.metadata_upload import views

urlpatterns = [
    url(_(r'^metadata_upload/$'), views.upload_files, name='upload_files'),
    url(_(r'^upload_success/$'), views.upload_success, name='upload_success'),
]
