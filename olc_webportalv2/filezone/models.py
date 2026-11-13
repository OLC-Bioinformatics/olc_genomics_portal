"""Database models for FileZone containers and searches."""

from django.contrib.postgres.fields import ArrayField
from django.db import models


class ContainerName(models.Model):
    """Store names used by the container autocomplete field."""

    container_name = models.CharField(max_length=64)

    def __str__(self):
        """Return the storage container name."""
        return self.container_name

    class Meta:
        """Grant users the custom permission to add containers."""

        permissions = [
            ('add', 'Can add containers'),
        ]


class Blobs(models.Model):
    """Store metadata for a blob in a container."""

    container_name = models.ForeignKey(
        ContainerName, on_delete=models.CASCADE, related_name='container'
    )
    blob_name = models.CharField(max_length=1024, blank=False)
    blob_size = models.CharField(max_length=64, blank=False)
    blob_date = models.DateTimeField()
    blob_download_link = models.CharField(max_length=256, blank=False)
    # blob_md5 = models.CharField(max_length=24, blank=False)

    def __str__(self):
        """Return the container and object path."""
        return f'{self.container_name}/{self.blob_name}'


class Regexes(models.Model):
    """Store a FileZone search request and its results."""

    container_regex = models.TextField(blank=True, null=True)
    container_exclude_regex = models.TextField(blank=True, null=True)
    created_at = models.DateField(auto_now_add=True)
    file_regex = models.TextField(blank=True, null=True)
    file_exclude_regex = models.TextField(blank=True, null=True)
    container_regex_list = ArrayField(
        models.CharField(max_length=255), default=list, blank=True, null=True
    )
    container_exclude_regex_list = ArrayField(
        models.CharField(max_length=255), default=list, blank=True, null=True
    )
    file_regex_list = ArrayField(
        models.CharField(max_length=255), default=list, blank=True, null=True
    )
    file_exclude_regex_list = ArrayField(
        models.CharField(max_length=255), default=list, blank=True, null=True
    )
    file_matches = models.JSONField(default=dict, blank=True, null=True)
    file_ajax = models.JSONField(default=dict, blank=True, null=True)
    status = models.CharField(max_length=64, default='Unprocessed')

    def __str__(self):
        """Return a stable display name for the search request."""
        return f'regexes-{self.pk}'
