from django.conf import settings
from django.conf.urls import include, url
from django.urls import path
from django.conf.urls.static import static
from django.contrib import admin
from django.views.generic import TemplateView
from django.views import defaults as default_views
from django.conf.urls.i18n import i18n_patterns

urlpatterns = [
    url(r'^$', TemplateView.as_view(template_name='pages/home.html'), name='home'),
    url(r'^about/$', TemplateView.as_view(template_name='pages/about.html'), name='about'),

    # Django Admin, use {% url 'admin:index' %}
    url(settings.ADMIN_URL, admin.site.urls),

    # Your stuff: custom urls includes go here
    path('i18n/', include('django.conf.urls.i18n')),
    path('api-auth/', include('rest_framework.urls'))
]
# Allows for url translation
urlpatterns += i18n_patterns(
    # User management
    url(r'^users/', include(('olc_webportalv2.users.urls', 'users'), namespace='users')),
    url(r'^accounts/', include('allauth.urls')),
    
    url(r'^cowbat/', include(('olc_webportalv2.cowbat.urls', 'cowbat'), namespace='cowbat')),
    url(r'^data/', include(('olc_webportalv2.data.urls', 'data'), namespace='data')),
    url(r'^geneseekr/', include(('olc_webportalv2.geneseekr.urls', 'geneseekr'), namespace='geneseekr')),
    url(r'^metadata/', include(('olc_webportalv2.metadata.urls', 'metadata'), namespace='metadata')),
    url(r'^vir_typer/', include(('olc_webportalv2.vir_typer.urls', 'vir_typer'), namespace='vir_typer')),
    url(r'^api/', include(('olc_webportalv2.api.urls', 'api'), namespace='api')),
    url(r'^sequence_database/', include(('olc_webportalv2.sequence_database.urls', 'sequence_database'),
                                        namespace='sequence_database')),
) + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) + \
               static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        url(r'^400/$', default_views.bad_request, kwargs={'exception': Exception('Bad Request!')}),
        url(r'^403/$', default_views.permission_denied, kwargs={'exception': Exception('Permission Denied')}),
        url(r'^404/$', default_views.page_not_found, kwargs={'exception': Exception('Page not Found')}),
        url(r'^500/$', default_views.server_error),
    ]
    if 'debug_toolbar' in settings.INSTALLED_APPS:
        import debug_toolbar
        urlpatterns = [
            url(r'^__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns
