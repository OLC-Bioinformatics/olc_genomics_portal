"""Database models for API authentication and desktop pairing."""

from django.conf import settings
from django.db import models


class EntraIdentity(models.Model):
    """Link an Entra tenant identity to a FoodPort user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    tenant_id = models.CharField(max_length=128)
    object_id = models.CharField(max_length=128)
    email = models.EmailField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Enforce uniqueness for each tenant/object identity pair."""

        unique_together = (("tenant_id", "object_id"),)


class DesktopAccessToken(models.Model):
    """Store an expiring access token for a desktop client."""

    key = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    created = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()


class DesktopPairing(models.Model):
    """Track a desktop pairing request through approval and exchange."""

    pairing_id = models.CharField(max_length=64, unique=True)
    code_hash = models.CharField(max_length=128, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    approved_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="desktop_pairings",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
