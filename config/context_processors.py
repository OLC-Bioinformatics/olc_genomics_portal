from django.conf import settings


def auth_settings(request):
    return {
        'MICROSOFT_AUTH_ENABLED': getattr(settings, 'MICROSOFT_AUTH_ENABLED', False),
    }
