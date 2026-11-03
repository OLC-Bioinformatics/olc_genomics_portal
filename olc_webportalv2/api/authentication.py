import binascii
import os

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from olc_webportalv2.api.models import DesktopAccessToken


class DesktopTokenAuthentication(BaseAuthentication):
    keyword = 'Token'

    def authenticate_header(self, request):
        return self.keyword

    def authenticate(self, request):
        header = request.META.get('HTTP_AUTHORIZATION', '')
        parts = header.split()
        if not parts or parts[0].lower() != self.keyword.lower():
            return None
        if len(parts) != 2:
            raise AuthenticationFailed('Invalid token header.')

        token = DesktopAccessToken.objects.select_related('user').filter(
            key=parts[1]
        ).first()
        if token is None:
            return None
        if token.expires_at <= timezone.now():
            raise AuthenticationFailed('Invalid or expired token.')
        if not token.user.is_active:
            raise AuthenticationFailed('User account is inactive.')
        return token.user, token


def new_desktop_token():
    return binascii.hexlify(os.urandom(32)).decode('ascii')