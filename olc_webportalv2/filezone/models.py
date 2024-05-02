from django.contrib.postgres.fields import (
    ArrayField,
    JSONField
)
from django.db import models


# Portal-specific imports
from olc_webportalv2.users.models import User


class ContainerName(models.Model):
    """
    Model to store the autocomplete container name
    """
    container_name = models.CharField(max_length=64)
    #user = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.container_name

    class Meta:
        """
        Add the "add" permission to users to allow them to create new containers
        """
        permissions = [
            ("add", "Can add containers"),
        ]


class Blobs(models.Model):
    """
    Model to store the name of blobs in the container
    """
    container_name = models.ForeignKey(
        ContainerName,
        on_delete=models.CASCADE,
        related_name='container')
    blob_name = models.CharField(max_length=1024, blank=False)
    blob_size = models.CharField(max_length=64, blank=False)
    blob_date = models.DateTimeField()
    blob_download_link = models.CharField(max_length=256, blank=False)
    # blob_md5 = models.CharField(max_length=24, blank=False)

    def __str__(self):
        return '{container}/{blob_name}'.format(
            container=self.container_name,
            blob_name=self.blob_name)


class Regexes(models.Model):
    """
    Model for user-supplied container and file regexes
    """
    container_regex = models.TextField(blank=True, null=True)
    container_exclude_regex = models.TextField(blank=True, null=True)
    created_at = models.DateField(auto_now_add=True)
    file_regex = models.TextField(blank=True, null=True)
    file_exclude_regex = models.TextField(blank=True, null=True)
    container_regex_list = ArrayField(
        models.CharField(max_length=255),
        default=list,
        blank=True,
        null=True
    )
    container_exclude_regex_list = ArrayField(
        models.CharField(max_length=255),
        default=list,
        blank=True,
        null=True
    )
    file_regex_list = ArrayField(
        models.CharField(max_length=255),
        default=list,
        blank=True,
        null=True
    )
    file_exclude_regex_list = ArrayField(
        models.CharField(max_length=255),
        default=list,
        blank=True,
        null=True
    )
    file_matches = JSONField(default=dict, blank=True, null=True)
    file_ajax = JSONField(default=dict, blank=True, null=True)
    status = models.CharField(max_length=64, default='Unprocessed')

    def __str__(self):
        return 'regexes-{pk}'.format(
            pk=self.pk,
        )
