from olc_webportalv2.sequence_database import views
from django.conf.urls import url, include
from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns
from rest_framework.schemas import get_schema_view
from django.utils.translation import gettext_lazy as _

schema_view = get_schema_view(title='Metadata API')

urlpatterns = [
    url(_(r'^database_filter/'), views.database_filter, name='database_filter'),
    url(_(r'^database_results/(?P<database_request_pk>\d+)/$'), views.database_results, name='database_results'),
    url(_(r'^database_filter_results/'), views.database_filter_results, name='database_filter_results'),
    url(_(r'^database_browse/'), views.database_browse, name='database_browse'),


    # Views for autocompletion
    url(r'^genus_autocompleter/$',
        views.GenusAutoCompleter.as_view(),
        name='genus_autocompleter'),
    url(r'^species_autocompleter/$',
        views.SpeciesAutoCompleter.as_view(),
        name='species_autocompleter'),
    url(r'^mlst_autocompleter/$',
        views.MLSTAutoCompleter.as_view(),
        name='mlst_autocompleter'),
    url(r'^mlstcc_autocompleter/$',
        views.MLSTCCAutoCompleter.as_view(),
        name='mlstcc_autocompleter'),
    url(r'^rmlst_autocompleter/$',
        views.RMLSTAutoCompleter.as_view(),
        name='rmlst_autocompleter'),
    # url(r'^geneseekr_autocompleter/$',
    #     views.GeneseekrAutoCompleter.as_view(),
    #     name='geneseekr_autocompleter'),
    # url(r'^serovar_autocompleter/$',
    #     views.SerovarAutoCompleter.as_view(),
    #     name='serovar_autocompleter'),
    # url(r'^vtyper_autocompleter/$',
    #     views.VtyperAutoCompleter.as_view(),
    #     name='vtyper_autocompleter'),

    # REST API Stuff
    path('sequencedata/', views.SequenceDataList.as_view()),
    path('sequencedata/<int:pk>/', views.SequenceDataDetail.as_view()),
    path('olndata/', views.OLNList.as_view()),
    path('olndata/<str:oln_id>/', views.OLNDetail.as_view()),
    path('schema/', schema_view),
]

urlpatterns = format_suffix_patterns(urlpatterns)
