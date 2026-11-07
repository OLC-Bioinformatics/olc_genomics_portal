import json

import jwt
import requests

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status, views
from rest_framework.authentication import TokenAuthentication
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.response import Response

from olc_webportalv2.api.authentication import (
    DesktopTokenAuthentication,
    new_desktop_token,
)
from olc_webportalv2.api.models import DesktopAccessToken, EntraIdentity


_jwks_cache = None


def _setting(name, default=''):
    return getattr(settings, name, default)


def _jwks():
    global _jwks_cache
    if _jwks_cache is None:
        response = requests.get(_setting('ENTRA_JWKS_URI'), timeout=5)
        response.raise_for_status()
        _jwks_cache = response.json()['keys']
    return _jwks_cache


def _refresh_jwks():
    global _jwks_cache
    _jwks_cache = None
    return _jwks()


def _validate_entra_token(raw_token):
    if not raw_token:
        raise ValueError('missing token')

    header = jwt.get_unverified_header(raw_token)
    key_data = next((key for key in _jwks()
                     if key.get('kid') == header.get('kid')), None)
    if key_data is None:
        key_data = next(
            key for key in _refresh_jwks()
            if key.get('kid') == header.get('kid')
        )
    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
    claims = jwt.decode(
        raw_token,
        key,
        algorithms=['RS256'],
        audience=_setting('ENTRA_API_AUDIENCE'),
        issuer=_setting('ENTRA_ISSUER'),
        leeway=30,
    )
    if claims.get('tid') != _setting('ENTRA_TENANT_ID'):
        raise ValueError('wrong tenant')
    if claims.get('ver') != '2.0':
        raise ValueError('wrong token version')
    client_id = _setting('ENTRA_DESKTOP_CLIENT_ID')
    if client_id and claims.get('azp', claims.get('appid')) != client_id:
        raise ValueError('wrong client')
    scopes = set((claims.get('scp') or '').split())
    if _setting('ENTRA_API_SCOPE') not in scopes:
        raise ValueError('missing scope')
    if not claims.get('oid'):
        raise ValueError('missing object id')
    return claims


def _allowed(claims):
    object_ids = set(filter(None, _setting(
        'ENTRA_DESKTOP_ALLOWED_OBJECT_IDS', ''
    ).split(',')))
    domains = set(filter(None, _setting(
        'ENTRA_DESKTOP_ALLOWED_EMAIL_DOMAINS', ''
    ).split(',')))
    email = (claims.get('preferred_username') or claims.get('email') or '').lower()
    return (
        claims['oid'] in object_ids or
        (domains and email.rsplit('@', 1)[-1] in domains)
    )


def _get_user(claims):
    User = get_user_model()
    tenant_id = claims['tid']
    object_id = claims['oid']
    email = (claims.get('preferred_username') or claims.get('email') or '').lower()
    identity = EntraIdentity.objects.select_related('user').filter(
        tenant_id=tenant_id, object_id=object_id
    ).first()
    if identity:
        if not identity.user.is_active:
            raise PermissionError('user is inactive')
        return identity.user
    if not _allowed(claims):
        raise PermissionError('user is not allowed')

    user = User.objects.filter(email__iexact=email).first() if email else None
    if user is None:
        if not _setting('ENTRA_DESKTOP_AUTO_CREATE_USERS', False):
            raise PermissionError('user is not provisioned')
        user = User(username=email, email=email, name=claims.get('name', ''))
        user.set_unusable_password()
        user.save()
    if not user.is_active:
        raise PermissionError('user is inactive')
    EntraIdentity.objects.create(
        user=user, tenant_id=tenant_id, object_id=object_id, email=email
    )
    return user


class EntraExchangeThrottle(SimpleRateThrottle):
    scope = 'entra_exchange'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }


class EntraDesktopExchangeView(views.APIView):
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (EntraExchangeThrottle,)

    def post(self, request):
        try:
            claims = _validate_entra_token(request.data.get('access_token'))
            with transaction.atomic():
                user = _get_user(claims)
                expires_in = int(_setting(
                    'FOODPORT_DESKTOP_TOKEN_TTL_SECONDS', 3600
                ))
                expires_in = max(60, min(expires_in, 86400))
                token = DesktopAccessToken.objects.create(
                    key=new_desktop_token(),
                    user=user,
                    expires_at=timezone.now() + timezone.timedelta(
                        seconds=expires_in
                    ),
                )
        except Exception:
            return Response(
                {'detail': 'Entra authentication failed.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response({
            'token': token.key,
            'expires_in': expires_in,
            'user': {
                'id': user.pk,
                'username': user.username,
                'display_name': user.get_full_name() or user.username,
            },
        })


class EntraDesktopLogoutView(views.APIView):
    authentication_classes = (DesktopTokenAuthentication, TokenAuthentication)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)