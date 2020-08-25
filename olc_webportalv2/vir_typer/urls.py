# Django-related imports
from django.conf.urls import url, include
from django.utils.translation import gettext_lazy as _
# VirusTyper-specific code
from olc_webportalv2.vir_typer import views

app_name = 'vir_typer'

urlpatterns = [
    # Vir_typer Stuff
    url(r'^$', views.vir_typer_home, name='vir_typer_home'),
    url(_(r'^create/'), views.vir_typer_request, name='vir_typer_request'),
    url(_(r'^upload/(?P<vir_typer_pk>\d+)/$'), views.vir_typer_upload, name='vir_typer_upload'),
    url(_(r'^results/(?P<vir_typer_pk>\d+)/$'), views.vir_typer_results, name='vir_typer_results'),
    url(_(r'^edit/(?P<vir_typer_pk>\d+)/$'), views.vir_typer_rename, name='vir_typer_rename'),
]
