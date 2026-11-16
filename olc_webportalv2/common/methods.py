#!/usr/bin/env python3

"""
Common methods shared between multiple apps
"""

# Standard imports
from copy import deepcopy
import json
import logging
import re
from typing import Optional

# Third-party imports
from celery import shared_task
import requests

# Django-related imports
from django.conf import settings


def _redact_sas_urls(*, value):
    """
    Redact query strings from URLs embedded in a logging value.

    The original value submitted to the Batch API is not modified. This
    helper is intended only for diagnostic logging and console output.

    Args:
        value (Any): Value that may contain one or more SAS URLs.

    Returns:
        Any: A logging-safe copy of the supplied value.
    """
    if isinstance(value, dict):
        return {
            key: _redact_sas_urls(value=item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _redact_sas_urls(value=item)
            for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            _redact_sas_urls(value=item)
            for item in value
        )

    if not isinstance(value, str):
        return value

    # Azure SAS URLs contain a query string beginning with "?". Preserve the
    # container and blob path for diagnostics while removing credentials.
    return re.sub(
        r"(https?://[^\s?'\"<>]+)\?[^\s'\"<>]+",
        r"\1?[REDACTED]",
        value,
    )


@shared_task
def generic_api_submit(
    command: str,
    container_name: str,
    vm_size: str,
    analysis_type: str = "COWBAT",
    input_file_pattern: Optional[list] = None,
    unique_id: Optional[str] = None,
    download_file_pattern: Optional[list] = None,
    log_prefix: Optional[str] = None
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

        download_file_pattern (list): File patterns to download from the VM.
        log_prefix (str): Prefix used to organize Batch task logs.

    Returns:
        dict: Batch submission response.
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
        "analysis_type": analysis_type,
        'unique_id': unique_id,
        'log_prefix': log_prefix
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
    response.raise_for_status()
    content_type = response.headers.get('content-type', '')
    if not content_type.lower().startswith('application/json'):
        raise RuntimeError(
            'Batch API returned a non-JSON response (HTTP {0}): {1}'.format(
                response.status_code,
                response.text[:1000],
            )
        )
    try:
        response_data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            'Batch API returned invalid JSON (HTTP {0})'.format(
                response.status_code
            )
        ) from exc

    # Log and print useful response attributes
    logging.info('Response status code: %s', response.status_code)
    logging.info('Response content: %s', response.content)
    logging.info('Response text: %s', response.text)
    logging.info('Response JSON: %s', response_data)
    logging.info('Response headers: %s', response.headers)
    logging.info('Response URL: %s', response.url)
    logging.info('Response elapsed time: %s', response.elapsed)
    logging.info('Response reason: %s', response.reason)

    print('Response status code:', response.status_code)
    print('Response content:', response.content)
    print('Response text:', response.text)
    print('Response JSON:', response_data)
    print('Response headers:', response.headers)
    print('Response URL:', response.url)
    print('Response elapsed time:', response.elapsed)
    print('Response reason:', response.reason)
    return response_data
