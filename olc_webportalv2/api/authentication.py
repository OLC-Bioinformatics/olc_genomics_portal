"""Authentication classes and token helpers for desktop clients."""

import binascii
import os

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from olc_webportalv2.api.models import DesktopAccessToken


class DesktopTokenAuthentication(BaseAuthentication):
    """Authenticate requests using expiring desktop access tokens."""

    keyword = "Token"

    def authenticate_header(self, request):
        """Return the authentication scheme advertised to clients.

        Args:
            request: Incoming HTTP request.

        """
        return self.keyword

    def authenticate(self, request):
        """Authenticate a request and return its user and token.

        Args:
            request: Incoming HTTP request.

        Returns:
            The authenticated user and token, or ``None`` if not applicable.

        Raises:
            AuthenticationFailed: If the supplied token is invalid or expired.

        """
        header = request.META.get("HTTP_AUTHORIZATION", "")
        parts = header.split()
        if not parts or parts[0].lower() != self.keyword.lower():
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Invalid token header.")

        token = (
            DesktopAccessToken.objects.select_related("user")
            .filter(key=parts[1])
            .first()
        )
        if token is None:
            return None
        if token.expires_at <= timezone.now():
            raise AuthenticationFailed("Invalid or expired token.")
        if not token.user.is_active:
            raise AuthenticationFailed("User account is inactive.")
        return token.user, token


def new_desktop_token():
    """Generate a cryptographically random desktop token."""
    return binascii.hexlify(os.urandom(32)).decode("ascii")
