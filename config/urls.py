"""
This module defines the URL configurations for the Django application.

The `urlpatterns` list routes URLs to views. For more information, please see:
https://docs.djangoproject.com/en/3.1/topics/http/urls/

The `i18n_patterns` function is used to add locale prefix to the specified URL
patterns. This allows for URL translation.

In debug mode, this module also configures additional URLs for error pages and
the Django Debug Toolbar.

Imports:
- `settings`: Module that contains the settings of your Django project.
- `include`, `url`, `path`: Functions to include and define URL patterns.
- `static`: Function to serve static files during development.
- `admin`: Module that provides the administrative interface.
- `TemplateView`: Class-based view to render a given template.
- `default_views`: Module that contains default views for error handlers.
- `i18n_patterns`: Function to add locale prefix to URL patterns.

Variables:
- `urlpatterns`: A list of URL patterns for the Django application.
"""

from django.conf import settings
from django.conf.urls import include, url
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path
from django.views import defaults as default_views
from django.views.generic import TemplateView

urlpatterns = [
    url(
        r'^$',
        TemplateView.as_view(template_name='pages/home.html'),
        name='home'
    ),
    url(
        r'^about/$',
        TemplateView.as_view(template_name='pages/about.html'),
        name='about'
    ),

    # Django Admin, use {% url 'admin:index' %}
    url(settings.ADMIN_URL, admin.site.urls),
]

if settings.MICROSOFT_AUTH_ENABLED:
    urlpatterns += [
        path(
            'microsoft_authentication/',
            include('microsoft_authentication.urls')
        ),
    ]

urlpatterns += [
    path('i18n/', include('django.conf.urls.i18n')),
    path('api-auth/', include('rest_framework.urls')),
]

# Your stuff: custom urls includes go here

# Allows for url translation
urlpatterns += i18n_patterns(
    url(
        r'^accounts/',
        include('allauth.urls')
    ),
    url(
        r'^ampliseq/',
        include(
            ('olc_webportalv2.ampliseq.urls', 'ampliseq'),
            namespace='ampliseq'
        )
    ),
    url(
        r'^api/',
        include(
            ('olc_webportalv2.api.urls', 'api'),
            namespace='api'
        )
    ),
    url(
        r'^cowbat/',
        include(
            ('olc_webportalv2.cowbat.urls', 'cowbat'),
            namespace='cowbat'
        )
    ),
    url(
        r'^cowsnphr/',
        include(
            ('olc_webportalv2.cowsnphr.urls', 'cowsnphr'),
            namespace='cowsnphr'
        )
    ),
    url(
        r'^data/',
        include(
            ('olc_webportalv2.data.urls', 'data'),
            namespace='data'
        )
    ),
    url(
        r'^filezone/',
        include(
            ('olc_webportalv2.filezone.urls', 'filezone'),
            namespace='filezone'
        )
    ),
    url(
        r'^geneseekr/',
        include(
            ('olc_webportalv2.geneseekr.urls', 'geneseekr'),
            namespace='geneseekr'
        )
    ),
    url(
        r'^metadata/',
        include(
            ('olc_webportalv2.metadata.urls', 'metadata'),
            namespace='metadata'
        )
    ),
    url(
        r'^primer_finder/',
        include(
            ('olc_webportalv2.primer_finder.urls', 'primer_finder'),
            namespace='primer_finder'
        )
    ),
    url(
        r'^sequence_database/',
        include(
            ('olc_webportalv2.sequence_database.urls', 'sequence_database'),
            namespace='sequence_database'
        )
    ),
    url(
        r'^users/',
        include(
            ('olc_webportalv2.users.urls', 'users'),
            namespace='users'
        )
    ),
    url(
        r'^vir_typer/',
        include(
            ('olc_webportalv2.vir_typer.urls', 'vir_typer'),
            namespace='vir_typer'
        )
    ),
    url(
        r'^metadata_upload/',
        include(
            ('olc_webportalv2.metadata_upload.urls', 'metadata_upload'),
            namespace='metadata_upload'
        )
    ),
) + static(
    settings.MEDIA_URL,
    document_root=settings.MEDIA_ROOT
) + static(
    settings.MEDIA_URL,
    document_root=settings.MEDIA_ROOT
)

if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        url(
            r'^400/$',
            default_views.bad_request,
            kwargs={
                'exception': Exception('Bad Request!')
            }
        ),
        url(
            r'^403/$',
            default_views.permission_denied,
            kwargs={
                'exception': Exception('Permission Denied')
            }
        ),
        url(
            r'^404/$',
            default_views.page_not_found, kwargs={
                'exception': Exception('Page not Found')
            }
        ),
        url(r'^500/$', default_views.server_error),
    ]
    if 'debug_toolbar' in settings.INSTALLED_APPS:
        import debug_toolbar
        urlpatterns = [
            url(r'^__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns
