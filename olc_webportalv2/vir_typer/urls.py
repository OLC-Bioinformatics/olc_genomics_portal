from django.utils.translation import gettext_lazy as _
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls import url, include

from olc_webportalv2.vir_typer import views

# app_name = 'vir_typer'
urlpatterns = [
    # Vir_typer Stuff
    url(r'^$', views.vir_typer_home, name='vir_typer_home'),
    url(_(r'^create/'), views.vir_typer_request, name='vir_typer_request'),
    url(_(r'^upload/(?P<vir_typer_pk>\d+)/$'), views.vir_typer_upload, name='vir_typer_upload'),
    url(_(r'^results/(?P<vir_typer_pk>\d+)/$'), views.vir_typer_results, name='vir_typer_results'),
    url(_(r'^edit/(?P<vir_typer_pk>\d+)/$'), views.vir_typer_rename, name='vir_typer_rename'),
    # url(r'^processing/(?P<vir_typer_pk>\d+)/$', views.vir_typer_processing, name='vir_typer_processing')
    # url(r'^geneseekr_query/', views.geneseekr_query, name='geneseekr_query'),
    # url(r'^geneseekr_processing/(?P<geneseekr_request_pk>\d+)/$', views.geneseekr_processing, name='geneseekr_processing'),
    # url(r'^geneseekr_results/(?P<geneseekr_request_pk>\d+)/$', views.geneseekr_results, name='geneseekr_results'),
    # url(r'^geneseekr_name/(?P<geneseekr_request_pk>\d+)/$', views.geneseekr_name, name='geneseekr_name'),

]
