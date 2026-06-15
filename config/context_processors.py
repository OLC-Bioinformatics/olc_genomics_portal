from django.conf import settings


def auth_settings(request):
    redirect_field_name = (
        request.GET.get('redirect_field_name')
        or request.POST.get('redirect_field_name')
        or 'next'
    )
    redirect_field_value = (
        request.GET.get(redirect_field_name)
        or request.POST.get(redirect_field_name)
        or request.GET.get('redirect_to')
        or request.POST.get('redirect_to')
        or ''
    )
    return {
        'MICROSOFT_AUTH_ENABLED': getattr(settings, 'MICROSOFT_AUTH_ENABLED', False),
        'redirect_field_name': redirect_field_name,
        'redirect_field_value': redirect_field_value,
        'redirect_to': request.GET.get('redirect_to', request.POST.get('redirect_to', '')),
    }
