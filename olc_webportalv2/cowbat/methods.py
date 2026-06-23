"""
Methods for COWBAT app
"""

# Azure imports
from azure.batch import BatchClient
from azure.core.credentials import AzureNamedKeyCredential

# Django imports
from django.conf import settings



def create_batch_client() -> BatchClient:
    """
    Creates a batch client using the settings from the Django settings file.
    :return: BatchClient object
    """
    credentials = AzureNamedKeyCredential(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )
    batch_client = BatchClient(
        credential=credentials,
        endpoint=settings.BATCH_ACCOUNT_URL
    )
    return batch_client
