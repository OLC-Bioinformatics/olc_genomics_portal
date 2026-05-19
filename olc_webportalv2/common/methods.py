#!/usr/bin/env python3

"""
Common methods shared between multiple apps
"""

# Standard imports
import json
import logging
from typing import Optional

# Third-party imports
from celery import shared_task
import requests

# Django-related imports
from django.conf import settings


@shared_task
def generic_api_submit(
    command: str,
    container_name: str,
    vm_size: str,
    analysis_type: str = "COWBAT",
    input_file_pattern: Optional[list] = None,
    unique_id: Optional[str] = None
) -> None:
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
        None
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
    logging.info('Response JSON: %s', response.json() if response.headers.get(
        'content-type') == 'application/json' else 'No JSON response')
    logging.info('Response headers: %s', response.headers)
    logging.info('Response URL: %s', response.url)
    logging.info('Response elapsed time: %s', response.elapsed)
    logging.info('Response reason: %s', response.reason)

    print('Response status code:', response.status_code)
    print('Response content:', response.content)
    print('Response text:', response.text)
    print('Response JSON:', response.json() if response.headers.get(
        'content-type') == 'application/json' else 'No JSON response')
    print('Response headers:', response.headers)
    print('Response URL:', response.url)
    print('Response elapsed time:', response.elapsed)
    print('Response reason:', response.reason)
