from olc_webportalv2.metadata import views
from django.conf.urls import url, include
from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns
from rest_framework.schemas import get_schema_view
from django.utils.translation import gettext_lazy as _

schema_view = get_schema_view(title='Metadata API')

urlpatterns = [
    url(_(r'^metadata_home/'), views.metadata_home, name='metadata_home'),
    url(_(r'^metadata_results/(?P<metadata_request_pk>\d+)/$'), views.metadata_results, name='metadata_results'),
    url(_(r'^metadata_browse/'), views.metadata_browse, name='metadata_browse'),
    url(_(r'^metadata_submit/'), views.metadata_submit, name='metadata_submit'),

    # Views for autocompletion
    url(r'^genus_autocompleter/$',
        views.GenusAutoCompleter.as_view(),
        name='genus_autocompleter'),
    url(r'^species_autocompleter/$',
        views.SpeciesAutoCompleter.as_view(),
        name='species_autocompleter'),
    url(r'^serotype_autocompleter/$',
        views.SerotypeAutoCompleter.as_view(),
        name='serotype_autocompleter'),
    url(r'^mlst_autocompleter/$',
        views.MLSTAutoCompleter.as_view(),
        name='mlst_autocompleter'),
    url(r'^rmlst_autocompleter/$',
        views.RMLSTAutoCompleter.as_view(),
        name='rmlst_autocompleter'),

    # REST API Stuff
    path('sequencedata/', views.SequenceDataList.as_view()),
    path('sequencedata/<int:pk>/', views.SequenceDataDetail.as_view()),
    path('olndata/', views.OLNList.as_view()),
    path('olndata/<str:oln_id>/', views.OLNDetail.as_view()),
    path('schema/', schema_view),
]

urlpatterns = format_suffix_patterns(urlpatterns)
