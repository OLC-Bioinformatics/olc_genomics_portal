from olc_webportalv2.metadata import views
from django.conf.urls import url, include

urlpatterns = [
    url(r'^metadata_home/', views.metadata_home, name='metadata_home'),
    url(r'^metadata_results/(?P<metadata_request_pk>\d+)/$', views.metadata_results, name='metadata_results'),

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
        name='rmlst_autocompleter')
]
