"""Desktop pairing endpoints and token-exchange helpers."""

import base64
import binascii
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from rest_framework import permissions, status, views
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from olc_webportalv2.api.authentication import new_desktop_token
from olc_webportalv2.api.models import DesktopAccessToken, DesktopPairing


def _random_urlsafe(size=32):
    """Generate a URL-safe random identifier.

    Args:
        size: Number of random bytes.

    """
    return (
        base64.urlsafe_b64encode(os.urandom(size)).rstrip(b"=").decode("ascii")
    )


def _pairing_code():
    """Generate a six-digit desktop pairing code."""
    code = int(binascii.hexlify(os.urandom(4)), 16) % 1000000
    return f"{code:06d}"


def _pairing_ttl():
    """Return the configured pairing lifetime, clamped to supported bounds."""
    return max(
        60,
        min(int(getattr(settings, "FOODPORT_PAIRING_TTL_SECONDS", 300)), 900),
    )


class PairingStartThrottle(SimpleRateThrottle):
    """Throttle pairing-start requests by client identity."""

    scope = "pairing_start"

    def get_cache_key(self, request, view):
        """Return the cache key for a pairing-start request.

        Args:
            request: Incoming HTTP request.
            view: API view handling the request.

        """
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class PairingExchangeThrottle(SimpleRateThrottle):
    """Throttle pairing-exchange requests by client identity."""

    scope = "pairing_exchange"

    def get_cache_key(self, request, view):
        """Return the cache key for a pairing-exchange request.

        Args:
            request: Incoming HTTP request.
            view: API view handling the request.

        """
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class PairingStartView(views.APIView):
    """Create a short-lived desktop pairing request."""

    permission_classes = (permissions.AllowAny,)
    throttle_classes = (PairingStartThrottle,)

    def post(self, request):
        """Create a pairing ID and return its approval URL.

        Args:
            request: Incoming HTTP request.

        """
        pairing_id = _random_urlsafe()
        expires_in = _pairing_ttl()
        pairing = DesktopPairing.objects.create(
            pairing_id=pairing_id,
            expires_at=timezone.now() + timezone.timedelta(seconds=expires_in),
        )
        return Response(
            {
                "pairing_id": pairing.pairing_id,
                "approval_url": request.build_absolute_uri(
                    reverse(
                        "localized_api:desktop_pairing_approval",
                        kwargs={"pairing_id": pairing.pairing_id},
                    )
                ),
                "expires_in": expires_in,
            },
            status=status.HTTP_201_CREATED,
        )


class PairingExchangeView(views.APIView):
    """Exchange an approved pairing code for an access token."""

    permission_classes = (permissions.AllowAny,)
    throttle_classes = (PairingExchangeThrottle,)

    def post(self, request):
        """Validate a pairing code and issue a desktop token.

        Args:
            request: Incoming HTTP request containing pairing ID and code.

        """
        pairing_id = request.data.get("pairing_id", "")
        code = request.data.get("code", "")
        try:
            with transaction.atomic():
                pairing = DesktopPairing.objects.select_for_update().get(
                    pairing_id=pairing_id
                )
                now = timezone.now()
                if (
                    pairing.used_at
                    or pairing.expires_at <= now
                    or not pairing.approved_user
                    or not check_password(code, pairing.code_hash)
                ):
                    raise ValueError("invalid pairing")
                expires_in = max(
                    60,
                    min(
                        int(
                            getattr(
                                settings,
                                "FOODPORT_DESKTOP_TOKEN_TTL_SECONDS",
                                3600,
                            )
                        ),
                        86400,
                    ),
                )
                token_user = pairing.approved_user
                token = DesktopAccessToken.objects.create(
                    key=new_desktop_token(),
                    user=token_user,
                    expires_at=now + timezone.timedelta(seconds=expires_in),
                )
                pairing.used_at = now
                pairing.save(update_fields=["used_at"])
        except Exception:
            return Response(
                {"detail": "Pairing authentication failed."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(
            {
                "token": token.key,
                "expires_in": expires_in,
                "user": {
                    "id": token_user.pk,
                    "username": token_user.username,
                    "display_name": token_user.get_full_name()
                    or token_user.username,
                },
            }
        )


@login_required
@require_http_methods(["GET", "POST"])
def pairing_approval(request, pairing_id):
    """Approve a desktop pairing request for the signed-in user.

    Args:
        request: Authenticated HTTP request.
        pairing_id: Identifier of the pairing request to approve.

    """
    pairing = get_object_or_404(DesktopPairing, pairing_id=pairing_id)
    if pairing.expires_at <= timezone.now() or pairing.used_at:
        raise Http404
    if request.method == "POST":
        with transaction.atomic():
            pairing = DesktopPairing.objects.select_for_update().get(
                pk=pairing.pk
            )
            if pairing.expires_at <= timezone.now() or pairing.used_at:
                raise Http404
            if pairing.approved_user_id:
                return render(
                    request,
                    "api/pairing_approval.html",
                    {
                        "pairing": pairing,
                        "already_approved": True,
                    },
                )
            code = _pairing_code()
            pairing.code_hash = make_password(code)
            pairing.approved_user = request.user
            pairing.approved_at = timezone.now()
            pairing.save(
                update_fields=["code_hash", "approved_user", "approved_at"]
            )
        return render(
            request,
            "api/pairing_approval.html",
            {
                "pairing": pairing,
                "code": code,
            },
        )
    return render(
        request,
        "api/pairing_approval.html",
        {
            "pairing": pairing,
            "already_approved": bool(pairing.approved_user_id),
        },
    )
