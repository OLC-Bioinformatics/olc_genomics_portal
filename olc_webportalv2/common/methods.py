#!/usr/bin/env python3

"""
Common methods shared between multiple apps
"""

# Standard imports
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote
from time import sleep
from typing import Optional
import json
import logging
import os
import smtplib

# Azure imports
from azure.batch import BatchClient
from azure.core.credentials import AzureNamedKeyCredential
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import (
    BlobServiceClient,
    BlobSasPermissions,
    ContainerSasPermissions,
    generate_blob_sas,
    generate_container_sas,
)

# Third-party imports
import requests

# Django-related imports
from django.conf import settings


def generic_api_submit(
    command: str,
    container_name: str,
    vm_size: str,
    analysis_type: str = "COWBAT",
    input_file_pattern: Optional[list] = None,
    unique_id: Optional[str] = None
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
        "download_file_pattern": None,
        "analysis_type": analysis_type,
        'unique_id': unique_id
    }

    # Log the dictionary
    logging.info(data)
    print(data)

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

    raise ValueError(
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


def create_blob_service() -> BlobServiceClient:
    """
    Creates a blob service client using the settings from the Django settings
    file.
    :return: BlobServiceClient object
    """
    blob_service_client = BlobServiceClient(
        account_url=
            f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net",
        credential=settings.AZURE_ACCOUNT_KEY
    )
    return blob_service_client


def create_blob_client(
    container_name: str,
    blob_name: str,
    blob_service_client: BlobServiceClient
) -> BlobServiceClient.get_blob_client:
    """
    Creates a blob client using the settings from the Django settings file.
    :param container_name: Name of the container
    :param blob_name: Name of the blob
    :return: BlobServiceClient object
    """
    return blob_service_client.get_blob_client(
        container=container_name,
        blob=blob_name
    )


def create_blob_from_path(
    blob_client: BlobServiceClient.get_blob_client,
    file_path: str
) -> None:
    """
    Creates a blob from a file path using the settings from the Django settings
    file.
    :param blob_client: Blob client object
    :param file_path: Path to the file to be uploaded
    :return: None
    """
    with open(file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True)


def create_blob_from_bytes(
    container_name: str,
    blob_name: str,
    data: bytes,
    blob_service_client: BlobServiceClient
) -> None:
    """
    Uploads a blob from bytes.

    :param container_name: Name of the container
    :param blob_name: Name of the blob
    :param data: Bytes to upload
    :param blob_service_client: BlobServiceClient instance
    :return: None
    """
    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=blob_name
    )
    blob_client.upload_blob(data, overwrite=True)


def upload_blob_from_path(
    container_name: str,
    blob_name: str,
    file_path: str,
    blob_service_client: BlobServiceClient
) -> None:
    """
    Uploads a blob from a local file path.

    :param container_name: Name of the container
    :param blob_name: Name of the blob
    :param file_path: Path to the local file
    :param blob_service_client: BlobServiceClient instance
    :return: None
    """

    # Ensure the container exists before uploading
    create_container(
        container_name=container_name,
        blob_service_client=blob_service_client
    )

    # Get the blob client for the specified container and blob
    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=blob_name
    )

    # Upload the file to the blob
    with open(file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True)


def download_blob_to_path(
    container_name: str,
    blob_name: str,
    file_path: str,
    blob_service_client: BlobServiceClient
) -> None:
    """
    Downloads a blob to a local file path.

    :param container_name: Name of the container
    :param blob_name: Name of the blob
    :param file_path: Local file path to write
    :param blob_service_client: BlobServiceClient instance
    :return: None
    """
    # Get the blob client for the specified container and blob
    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=blob_name
    )

    # Ensure the directory exists before downloading
    dir_name = os.path.dirname(file_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    # Download the blob to the specified file path
    with open(file_path, "wb") as local_file:
        blob_client.download_blob().readinto(local_file)



def generate_download_link(
    blob_service_client: BlobServiceClient,
    container_name: str,
    blob_name: str,
    expiry: int = 8,
    content_disposition: Optional[str] = None
) -> str:
    """
    Generate a read-only SAS URL for a blob.

    :param blob_service_client: BlobServiceClient instance
    :param container_name: Name of the container
    :param blob_name: Name of the blob
    :param expiry: Number of days to keep the URL valid
    :param content_disposition: Optional content disposition header
    :return: SAS URL string
    """
    sas_token = generate_blob_sas(
        account_name=settings.AZURE_ACCOUNT_NAME,
        container_name=container_name,
        blob_name=blob_name,
        account_key=settings.AZURE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(days=expiry),
        content_disposition=content_disposition,
    )

    # Return the full URL with the SAS token
    return (
        f"{blob_service_client.url}/"
        f"{container_name}/"
        f"{quote(blob_name, safe='/')}?"
        f"{sas_token}"
    )


def generate_container_download_link(
    blob_service_client: BlobServiceClient,
    container_name: str,
    expiry: int = 8
) -> str:
    """
    Generate a read-only SAS URL for a container.

    :param blob_service_client: BlobServiceClient instance
    :param container_name: Name of the container
    :param expiry: Number of days the link should be valid
    :return: SAS URL string
    """
    sas_token = generate_container_sas(
        account_name=settings.AZURE_ACCOUNT_NAME,
        container_name=container_name,
        account_key=settings.AZURE_ACCOUNT_KEY,
        permission=ContainerSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(days=expiry),
    )
    return f"{blob_service_client.url}/{container_name}?{sas_token}"


def create_container(
    container_name: str,
    blob_service_client: BlobServiceClient
) -> None:
    """
    Create a container if it does not already exist.
    """
    try:
        blob_service_client.create_container(container_name)
    except ResourceExistsError:
        pass


def download_container(
    blob_service_client: BlobServiceClient,
    container_name: str,
    output_dir: str
) -> None:
    """
    Download all blobs in a container to a local directory, preserving folder structure.

    :param blob_service_client: BlobServiceClient instance
    :param container_name: Name of the container
    :param output_dir: Local directory to write blobs to
    :return: None
    """
    container_client = blob_service_client.get_container_client(container_name)
    os.makedirs(output_dir, exist_ok=True)
    for blob in container_client.list_blobs():
        blob_path = blob.name

        # Construct the full local path for the blob
        destination_path = os.path.join(output_dir, blob_path)

        # Ensure the destination directory exists
        destination_dir = os.path.dirname(destination_path)
        if destination_dir:
            os.makedirs(destination_dir, exist_ok=True)

        # Download the blob to the destination path
        download_blob_to_path(
            container_name=container_name,
            blob_name=blob_path,
            file_path=destination_path,
            blob_service_client=blob_service_client,
        )
