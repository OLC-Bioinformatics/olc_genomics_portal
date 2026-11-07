from django.conf import settings
from django.db import models


class EntraIdentity(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant_id = models.CharField(max_length=128)
    object_id = models.CharField(max_length=128)
    email = models.EmailField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (('tenant_id', 'object_id'),)


class DesktopAccessToken(models.Model):
    key = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()


class DesktopPairing(models.Model):
    pairing_id = models.CharField(max_length=64, unique=True)
    code_hash = models.CharField(max_length=128, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    approved_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='desktop_pairings',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
