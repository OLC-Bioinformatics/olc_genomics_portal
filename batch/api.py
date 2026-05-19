#!/usr/bin/env python

"""
This module is a Flask application that serves as a wrapper for the Azure Batch
package. It handles API calls and runs a Flask server.

The application exposes a single endpoint, `/submit_batch_request`, which
accepts POST requests. The POST request should contain a JSON object with
details required to submit a batch job to Azure Batch.

The endpoint creates an `AzureBatch` object with the data from the request,
calls its `main` method to submit a batch job to Azure Batch, waits for the
job to complete, and returns the job status.

The module is designed to be run as a standalone script, but the
`submit_batch_request` function can also be imported and used in other
Python programs.
"""

# Standard imports
import logging
import os
from pathlib import Path
import shutil
import uuid

# Third-party imports
from azure_batch.azure_cli import (
    AzureBatch,
    Settings
)

from dotenv import load_dotenv

from flask import (
    Flask,
    jsonify,
    request
)

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize a new Flask application instance. The argument __name__ is a
# special variable in Python that is automatically set to the name of the
# module in which it is used. Flask uses the location of the module passed
# here as a starting point when it needs to load associated resources such
# as template files, which can be useful when you want to make your
# application reusable.
app = Flask(__name__)


@app.route('/')
def home():
    """
    This function is a simple route that returns a greeting message.
    """
    return "Hello, World!"


@app.route('/submit_batch_request', methods=['POST'])
def submit_batch_request():
    """
    This script is a wrapper for the Azure Batch package. It handles
    API calls and runs a Flask application.

    The Flask application exposes a single endpoint,
    `/submit_batch_request`, which accepts POST requests. The POST
    request should contain a JSON object with the following properties:

    - `container`: The name of the Azure container.
    - `command_file`: The command to be written to a local file.
    - `vm_size`: The size of the virtual machine.
    - `path`: The path in the Azure container where the input files
    are located.
    - `input_file_pattern`: The pattern to match the input files in
    the Azure container.
    - `download_file_pattern`: The pattern to match the files to
    download from the Azure container.
    - `no_tidy` (optional): A boolean value that indicates whether
    to tidy up the Azure Batch resources after the job is done. If
    this property is not provided, it defaults to `True`.

    The endpoint creates an `AzureBatch` object with the data from
    the request and calls its `main` method. The `main` method
    submits a batch job to Azure Batch, waits for the job to
    complete, and returns the job status.

    The endpoint returns a JSON object with the following properties:

    - `pool_id`: The ID of the Azure Batch pool.
    - `job_id`: The ID of the Azure Batch job.
    - `tasks`: The tasks in the Azure Batch job.
    - `status`: The status of the Azure Batch job.
    - `error`: Any error that occurred while running the Azure Batch
    job.
    """
    # Get the data from the request
    data = request.get_json()

    # Define the list of required fields
    required_fields = [
        'container',
        'command_file',
        'vm_size',
        'input_file_pattern',
        'download_file_pattern',
        'analysis_type'
    ]

    # Create a list of fields that are in required_fields but not in data
    missing_fields = [field for field in required_fields if field not in data]

    # If there are any missing fields, return an error message
    if missing_fields:
        return jsonify({
            'error': 'Missing required fields',
            'fields': missing_fields
        }), 400

    # Generate a random hash
    random_hash = uuid.uuid4().hex[:8]

    # Set the base path
    base_path = os.path.join('/app', random_hash)

    # Create the base path if it doesn't exist
    os.makedirs(base_path, exist_ok=True)

    # Set the absolute path of the local file to store the command
    command_file_path = os.path.join(
        base_path,
        f"{data['container']}_command_file.txt"
    )

    # Log the command and container
    logging.info(
        "Command supplied: %s, container name %s",
        data['command_file'], data['container']
    )

    # Write the contents of data['command_file'] to a local file
    with open(command_file_path, 'w', encoding='utf-8') as file:
        file.write(data['command_file'])

    # Write the contents of data['input_file_pattern'] to a local file
    if data['input_file_pattern'] is not None:
        # Set the absolute path of the input file
        input_file_path = os.path.join(
            base_path,
            "input.txt"
        )
        with open(input_file_path, 'w', encoding='utf-8') as file:
            for pattern in data['input_file_pattern']:
                file.write(' '.join(pattern) + '\n')
    else:
        input_file_path = None

    # Create a default value of None for unique_id
    unique_id = data.get('unique_id', None)

    # Resolve env file from parent of this script's directory (repo root).
    # load_dotenv with override=False means env vars already in the environment
    # (e.g. injected by Docker Compose) take precedence over the file.
    dotenv_path = Path(__file__).resolve().parent.parent / 'env'
    load_dotenv(dotenv_path=dotenv_path, override=False)
    settings_dict = dict(os.environ)
    local_settings = Settings(
        settings=settings_dict,
        analysis_type=data['analysis_type']
    )

    # Ensure that the provided analysis_type is one of the supported
    # analysis types
    if not local_settings.vm_image:
        logging.error(
            "Provided analysis type, %s, is not supported",
            data['analysis_type']
        )
        # Delete the base path
        shutil.rmtree(base_path)
        return jsonify(
            {
                'error': f'Invalid analysis type: {data["analysis_type"]}',
                'status': 'Failure',
                'pool_id': 'None',
                'job_id': 'None',
                'tasks': 'None',
            }
        ), 500

    # Allow for default action if 'no_tidy' is not provided
    no_tidy = data.get('no_tidy', False)

    # Create the AzureBatch object with the data from the request
    try:
        azure_batch = AzureBatch(
            command_file=command_file_path,
            vm_size=data['vm_size'],
            settings=local_settings,
            container=data['container'],
            path='/app',
            bulk_input_file_pattern=input_file_path,
            download_file_pattern=data['download_file_pattern'],
            worker=True,
            unique_id=unique_id,
            no_tidy=no_tidy
        )
        # Call the main method
        response = azure_batch.main()
        print("azure_batch.main() response: ", response)
    except Exception as exc:
        logging.error("Failed to create AzureBatch object: %s", exc)
        # Delete the base path
        shutil.rmtree(base_path)
        return jsonify(
            {
                'error': f'Failed to create AzureBatch object: {exc}',
                'status': 'Failure',
                'pool_id': 'None',
                'job_id': 'None',
                'tasks': 'None',
            }
        ), 500

    # Delete the base path
    shutil.rmtree(base_path)

    # Return the pool_id, job_id, tasks, status, and error
    return response
