"""
Create URLs for FileZone app
"""

from django.conf.urls import url
try:
    from django.utils.translation import gettext_lazy as _
except ImportError:
    from django.utils.translation import ugettext_lazy as _

# Third party imports
from dal import autocomplete
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.filezone import views
from olc_webportalv2.filezone.models import ContainerName

urlpatterns = [
    url(_(r'^filezone/'), views.filezone_home, name='filezone'),
    url(_(r'^container_select/'), views.container_select, name='container_select'),
    url(_(r'^container_create/'), views.container_create, name='container_create'),
    url(_(r'^container_view/(?P<filezone_pk>\d+)/$'),
        views.container_view,
        name='container_view'),
    url(_(r'^file_select/'), views.file_select, name='file_select'),
    url(_(r'^filezone_processing/(?P<filezone_pk>\d+)/$'),
        views.filezone_processing,
        name='filezone_processing'),
    url(_(r'^file_view/(?P<filezone_pk>\d+)/$'),
        views.file_view,
        name='file_view'),
    # Allow user to refresh containers for autocomplete
    url(_(r'^container_refresh/'), views.container_refresh, name='refresh'),
    # Views for autocompletion create_field='container_name', validate_create=True model=ContainerName
    url(r'^container_autocompleter/$',
        views.FileZoneAutoCompleter.as_view(),
        name='filezone_autocompleter'),
    # url(
    #     r'^container_autocompleter/$',
    #     views.CountryAutocomplete.as_view(create_field='name', validate_create=True),
    #     name='container_autocompleter',
    # ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
