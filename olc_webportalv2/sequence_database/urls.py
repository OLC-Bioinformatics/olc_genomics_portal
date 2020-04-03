from olc_webportalv2.sequence_database import views
from django.conf.urls import url
from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns
from rest_framework.schemas import get_schema_view
from django.utils.translation import gettext_lazy as _
schema_view = get_schema_view(title='Metadata API')

urlpatterns = [
    url(_(r'^database_home/'), views.database_home, name='database_home'),
    url(_(r'^database_filter/'), views.database_filter, name='database_filter'),
    url(_(r'^database_query/$'), views.database_query, name='database_query'),
    url(_(r'^id_search/'), views.id_search, name='id_search'),


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

    # REST API Stuff
    path('sequencedata/', views.SequenceDataList.as_view()),
    path('sequencedata/<int:pk>/', views.SequenceDataDetail.as_view()),
    path('olndata/', views.OLNList.as_view()),
    path('olndata/<str:oln_id>/', views.OLNDetail.as_view()),
    path('schema/', schema_view),
]

urlpatterns = format_suffix_patterns(urlpatterns)
