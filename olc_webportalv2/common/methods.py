#!/usr/bin/env python3

"""
Common methods shared between multiple apps
"""

# Standard imports
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from time import sleep
from copy import deepcopy
from dataclasses import dataclass
from typing import Optional
import json
import logging
import os
import re
import smtplib
from urllib.parse import urlsplit, urlunsplit

# Third-party imports
import requests

# Django-related imports
from django.conf import settings
from olc_webportalv2.common.batch_client import BatchServiceClient
from olc_webportalv2.common.storage_client import StorageClient, get_storage_client


def _redact_sas_urls(value):
    """Redact URL query strings while preserving nested data structures."""
    if isinstance(value, str):
        def redact(match):
            parts = urlsplit(match.group(0))
            if not parts.query:
                return match.group(0)
            return urlunsplit((
                parts.scheme,
                parts.netloc,
                parts.path,
                "[REDACTED]",
                parts.fragment,
            ))

        return re.sub(r"https?://[^\s\"'<>]+", redact, value)
    if isinstance(value, dict):
        return {key: _redact_sas_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sas_urls(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sas_urls(item) for item in value)
    return value


def generic_api_submit(
    command: str,
    container_name: str,
    vm_size: str,
    analysis_type: str = "COWBAT",
    input_file_pattern: Optional[list] = None,
    unique_id: Optional[str] = None,
    download_file_pattern: Optional[list] = None,
    output_file_pattern: Optional[list] = None,
    output_container: Optional[str] = None,
    output_prefix: Optional[str] = None,
    log_prefix: Optional[str] = None,
    environment_variables: Optional[dict] = None,
) -> dict:
    """
    Submits a job to the Batch API.

    This function sends a POST request to the Batch API with the specified
    command, container name, and VM size. 

    Args:
        analysis_type (str): The type of analysis to be run. This is used to
            select the appropriate VM image. Default is COWBAT
        command (str): The command to be run.
        container_name (str): The name of the container in which the files to
            be used in the analyses are located
        input_file_pattern (list): List of files not already in the container
            to be copied to the VM. Default is None
        vm_size (str): The size of the VM where the command will be run.
        unique_id (str): The unique ID to add to the container name rather than
            using a random hash. Default is None

    Returns:
        dict: The response from the Batch API as a dictionary.
    """
    # Ensure that empty lists are converted to None
    input_file_pattern = input_file_pattern if input_file_pattern else None

    # Define the data
    data = {
        "container": container_name,
        "command_file": command,
        "vm_size": vm_size,
        "input_file_pattern": input_file_pattern,
        "download_file_pattern": download_file_pattern,
        "output_file_pattern": output_file_pattern,
        "output_container": output_container,
        "output_prefix": output_prefix,
        "log_prefix": log_prefix,
        "environment_variables": environment_variables or {},
        "analysis_type": analysis_type,
        "unique_id": unique_id,
    }

    # Log the dictionary
    logging_data = _redact_sas_urls(value=deepcopy(data))
    logging.info(logging_data)
    print(logging_data)

    # Make the POST request with a timeout of 10 minutes
    response = requests.post(
        settings.BATCH_SERVICE_URL,
        headers=settings.BATCH_URL_HEADERS,
        data=json.dumps(data),
        timeout=600
    )

    # Log and print useful response attributes
    logging.info('Response status code: %s', response.status_code)
    logging.info('Response content: %s', response.content)
    logging.info('Response text: %s', response.text)
    
    # Check if the response is JSON and log accordingly
    content_type = response.headers.get('content-type', '')
    try:
        response_json = response.json() if 'application/json' in \
            content_type else 'No JSON response'
    except ValueError:
        response_json = 'Invalid JSON response'

    logging.info('Response JSON: %s', response_json)
    logging.info('Response headers: %s', response.headers)
    logging.info('Response URL: %s', response.url)
    logging.info('Response elapsed time: %s', response.elapsed)
    logging.info('Response reason: %s', response.reason)

    print('Response status code:', response.status_code)
    print('Response content:', response.content)
    print('Response text:', response.text)
    print('Response JSON:', response_json)
    print('Response headers:', response.headers)
    print('Response URL:', response.url)
    print('Response elapsed time:', response.elapsed)
    print('Response reason:', response.reason)

    response.raise_for_status()  # Raise an error for bad responses

    # Check if the response is a JSON object and return it, otherwise
    # raise an error
    if isinstance(response_json, dict):
        return response_json

    raise RuntimeError(
        f"Batch API did not return a JSON object. "
        f"content_type={content_type!r}, response_json={response_json!r}"
    )



def send_email(
    *,  # Enforce keyword arguments
    subject: str,
    body: str,
    recipient: str
) -> None:
    """
    Sends an email with the given subject, body, and recipient.

    If an "Access denied" SMTP data error or a "wrong version number" SMTP
    server disconnected error occurs, the function will wait for 5 seconds and
    then retry the operation. This retry process will happen up to 50 times.
    If any other error occurs, it will be raised immediately.

    Args:
        subject (str): The subject of the email.
        body (str): The body of the email.
        recipient (str): The recipient's email address.

    Raises:
        smtplib.SMTPDataError: If an SMTP data error occurs that is not an
        "Access denied" error.
        smtplib.SMTPServerDisconnected: If an SMTP server disconnected error
        occurs that is not a "wrong version number" error.
    """
    # Define the sender's email address
    from_addr = \
        'cfia.foodport.donotreply-nepasrepondre.aliport.acia@inspection.gc.ca'
    # Define the recipient's email address
    to_addr = recipient

    # Create a MIME multipart message
    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    # Attempt to send the email up to 50 times
    for _ in range(50):
        server = None  # Initialize server variable
        try:
            # Connect to the SMTP server
            server = smtplib.SMTP('email-smtp.ca-central-1.amazonaws.com', 587)

            # Start TLS encryption
            server.starttls()

            # Login to the SMTP server
            server.login(
                user=os.environ.get('EMAIL_HOST_USER'),
                password=os.environ.get('EMAIL_HOST_PASSWORD')
            )

            # Convert the message to a string
            text = msg.as_string()

            # Send the email
            server.sendmail(from_addr, to_addr, text)

            # If the email is sent successfully, break out of the loop
            break
        except smtplib.SMTPDataError as exc:
            # If an SMTP data error occurs...
            if exc.smtp_code == 554 and b"Access denied" in exc.smtp_error:
                # If the error is an "Access denied" error, print a message
                # and wait for 5 seconds before retrying
                print("Access denied error occurred, retrying...")
                sleep(5)
            else:
                # If it's a different error, re-raise it
                raise
        except smtplib.SMTPServerDisconnected as exc:
            # If the SMTP server gets disconnected...
            if "wrong version number" in str(exc):
                # If the error is a "wrong version number" error, print a
                # message, wait for 5 seconds, and reconnect to the server
                print("Wrong version number error occurred, retrying...")
                sleep(5)
            else:
                # If it's a different error, re-raise it
                raise
        finally:
            # Close the connection to the SMTP server
            if server is not None:
                server.quit()


def create_batch_client() -> BatchServiceClient:
    """
    Creates an HTTP client for the internal Batch service.
    """
    return BatchServiceClient()


def create_blob_service() -> StorageClient:
    """Return the provider-neutral client for the internal Storage service."""
    return get_storage_client()


@dataclass(frozen=True)
class ObjectReference:
    client: StorageClient
    container: str
    name: str


def create_blob_client(
    container_name: str,
    blob_name: str,
    blob_service_client: StorageClient
) -> ObjectReference:
    return ObjectReference(blob_service_client, container_name, blob_name)


def create_blob_from_path(blob_client: ObjectReference, file_path: str) -> None:
    blob_client.client.upload_file(
        blob_client.container,
        blob_client.name,
        file_path,
    )


def create_blob_from_bytes(
    container_name: str,
    blob_name: str,
    data: bytes,
    blob_service_client: StorageClient
) -> None:
    """
    Uploads a blob from bytes.

    :param container_name: Name of the container
    :param blob_name: Name of the blob
    :param data: Bytes to upload
    :param blob_service_client: Storage service client
    :return: None
    """
    blob_service_client.upload_bytes(container_name, blob_name, data)


def upload_blob_from_path(
    container_name: str,
    blob_name: str,
    file_path: str,
    blob_service_client: StorageClient
) -> None:
    """
    Uploads a blob from a local file path.

    :param container_name: Name of the container
    :param blob_name: Name of the blob
    :param file_path: Path to the local file
    :param blob_service_client: Storage service client
    :return: None
    """

    blob_service_client.ensure_container(container_name)
    blob_service_client.upload_file(container_name, blob_name, file_path)


def download_blob_to_path(
    container_name: str,
    blob_name: str,
    file_path: str,
    blob_service_client: StorageClient
) -> None:
    """
    Downloads a blob to a local file path.

    :param container_name: Name of the container
    :param blob_name: Name of the blob
    :param file_path: Local file path to write
    :param blob_service_client: Storage service client
    :return: None
    """
    blob_service_client.download_file(container_name, blob_name, file_path)



def generate_download_link(
    blob_service_client: StorageClient,
    container_name: str,
    blob_name: str,
    expiry: int = 8,
    content_disposition: Optional[str] = None
) -> str:
    return blob_service_client.create_transfer_url(
        container_name,
        "read",
        name=blob_name,
        expires_in_seconds=expiry * 86400,
        content_disposition=content_disposition,
    )


def generate_container_download_link(
    blob_service_client: StorageClient,
    container_name: str,
    expiry: int = 8
) -> str:
    """
    Generate a read-only SAS URL for a container.

    :param blob_service_client: Storage service client
    :param container_name: Name of the container
    :param expiry: Number of days the link should be valid
    :return: SAS URL string
    """
    return blob_service_client.create_transfer_url(
        container_name,
        "read",
        expires_in_seconds=expiry * 86400,
    )


def create_container(
    container_name: str,
    blob_service_client: StorageClient
) -> None:
    """
    Create a container if it does not already exist.
    """
    blob_service_client.ensure_container(container_name)


def download_container(
    blob_service_client: StorageClient,
    container_name: str,
    output_dir: str
) -> None:
    """
    Download all blobs in a container to a local directory, preserving folder structure.

    :param blob_service_client: Storage service client
    :param container_name: Name of the container
    :param output_dir: Local directory to write blobs to
    :return: None
    """
    os.makedirs(output_dir, exist_ok=True)
    for blob in blob_service_client.list_objects(container_name):
        blob_path = blob.name

        # Construct the full local path for the blob
        destination_path = os.path.join(output_dir, blob_path)

        # Ensure the destination directory exists
        destination_dir = os.path.dirname(destination_path)
        if destination_dir:
            os.makedirs(destination_dir, exist_ok=True)

        # Download the blob to the destination path
        blob_service_client.download_file(
            container_name,
            blob_path,
            destination_path,
        )
