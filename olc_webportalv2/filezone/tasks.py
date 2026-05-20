"""
Methods for FileZone app
"""

# Standard imports
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor, as_completed
import fnmatch
import json
import logging
import os
import sys
import zipfile

# Django imports
from django.conf import settings
from django.db import DatabaseError
# Azure imports
from azure.common import AzureMissingResourceHttpError, AzureHttpError
try:
    from azure.storage.blob import BlobServiceClient as BlockBlobService
except ImportError:
    from azure.storage.blob import BlockBlobService

# Celery Task Management
from celery import shared_task

# Sentry
from sentry_sdk import capture_exception

# Portal-specific imports
from olc_webportalv2.cowbat.tasks import (
    generate_download_link as generate_archive_download_link
)
from olc_webportalv2.filezone.methods import (
    FileLocate,
    generate_sas,
    human_bytes
)
from olc_webportalv2.filezone.models import (
    ContainerName,
    Regexes
)

# Set the logging levels
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def find_containers():
    """
    Find all containers
    """
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    containers = blob_client.list_containers()
    # Use a set comprehension to get the container names
    container_set = {str(container.name) for container in containers}
    return sorted(container_set)


@shared_task
def refresh_container_names():
    """
    Clear out all the container names from the model, and refresh
    """
    try:
        # Autocompletion: clear out all the previous data
        ContainerName.objects.all().delete()
        # Find all the blobs that match the naming format
        container_list = find_containers()
        # Add the blobs to the model
        ContainerName.objects.bulk_create(
            ContainerName(
                container_name=container
            ) for container in container_list
        )
    except (
            AzureMissingResourceHttpError,
            AzureHttpError,
            DatabaseError) as exc:
        capture_exception(exc)


def create_container(container_name):
    """
    Create a container in Azure storage
    """
    try:
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )
        blob_client.create_container(container_name)
    except Exception as exc:
        return exc


def calculate_md5_parallel(blob_client, blob, container_name):
    """
    Calculate MD5 in parallel for a given blob. Attempts to use the MD5
    provided by Azure, and falls back to manual calculation if necessary.

    :param blob_client: Azure BlobServiceClient instance
    :param blob: Blob object for which to calculate MD5
    :param container_name: Name of the container containing the blob
    :return: MD5 hash of the blob as a hexadecimal string
    """
    try:
        # Attempt to use the MD5 provided by Azure
        return binascii.hexlify(
            bytearray(
                base64.b64decode(
                    blob.properties.content_settings.content_md5
                )
            )
        ).decode()
    except TypeError:
        # Fallback to manual MD5 calculation if Azure's MD5 is unavailable
        return FileLocate.calculate_md5(
            blob_client=blob_client,
            blob_file=blob,
            container_name=container_name
        )


def list_blobs(filezone_pk):
    """
    List all blobs in a specified container and calculate their MD5 hashes
    in parallel, improving performance for containers with many files.

    :param filezone_pk: Primary key of the FileZone object representing
                        the container
    :return: List of dictionaries with blob details
    """
    # Initialize Azure Blob Service client with account details
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Retrieve container name using the FileZone primary key
    container = ContainerName.objects.get(pk=filezone_pk)

    # List all blobs in the specified container
    blobs = blob_client.list_blobs(container_name=container.container_name)

    # Filter out zero-size blobs and sort by blob name
    blobs = sorted(
        [blob for blob in blobs if blob.properties.content_length > 0],
        key=lambda blob: blob.name
    )

    blob_data = []
    pk_to_blob_dict = {}  # Mapping of pk to blob dictionary

    # Pre-assign pks based on sorted order
    for index, blob in enumerate(blobs, start=1):
        file_name = blob.name.split('/')[-1]
        blob_dict = {
            'pk': index,
            'blob_name': file_name,
            'blob_full_path': blob.name,
            # Placeholder values, will be updated after MD5 calculation
            'blob_size': None,
            'blob_date': None,
            'blob_download_link': None,
            'blob_md5': None
        }
        blob_data.append(blob_dict)
        pk_to_blob_dict[index] = blob_dict

    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Submit tasks for parallel MD5 calculation
        future_to_blob_index = {
            executor.submit(
                calculate_md5_parallel,
                blob_client,
                blob,
                container.container_name
                ): index for index, blob in enumerate(blobs, start=1)
        }

        for future in as_completed(future_to_blob_index):
            index = future_to_blob_index[future]
            blob_md5 = future.result()
            blob = blobs[index - 1]  # Adjust index to 0-based for list access

            # Update blob details with results from parallel processing
            blob_data[index - 1].update({
                'blob_size': human_bytes(
                    byte_count=blob.properties.content_length
                ),
                'blob_date': blob.properties.last_modified.strftime(
                    "%Y/%m/%d, %H:%M:%S"
                ),
                'blob_download_link': generate_sas(
                    blob_name=blob.name,
                    container_name=container.container_name
                ),
                'blob_md5': blob_md5
            })

    # Calculate blob hierarchy
    blob_hierarchy = organize_blobs_into_hierarchy(blob_data)

    return blob_hierarchy, pk_to_blob_dict


def organize_blobs_into_hierarchy(blob_data):
    """
    Organize flat blob data into a hierarchical structure based on the full
    path of blobs.

    :param blob_data: List of dictionaries with blob details.
    :return: Hierarchical structure of blobs.
    """
    root = {}

    for blob in blob_data:
        # Use 'blob_full_path' to split and navigate through the hierarchy
        path_parts = blob['blob_full_path'].split('/')
        current_level = root

        for part in path_parts[:-1]:  # Navigate through folders
            if part not in current_level:
                current_level[part] = {'__type__': 'folder', 'contents': {}}
            current_level = current_level[part]['contents']

        # Add the blob/file to the correct place in the hierarchy
        # Still using the last part of 'blob_full_path' to ensure uniqueness
        current_level[path_parts[-1]] = blob

    return root


def sanitise_rename(blob_rename):
    """
    Ensure that the supplied text is valid for renaming
    """
    invalid_chars = "^@!#&<>{}[]\\~`]*$\'\"%"
    return set(invalid_chars).intersection(blob_rename)


def rename_blob(blob_name, blob_rename, container_name):
    """
    Rename a blob with user-supplied text
    """
    errors = []
    # Use the AzureStorage library if available
    if 'azure_storage' in sys.modules:
        pass
    # Otherwise copy the blob using the new name, and delete the original
    else:
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )
        try:
            # Create the new blob
            blob_client.copy_blob(
                container_name,
                blob_rename,
                'https://{account_name}.blob.core.windows.net/'
                '{source_container_name}/'
                '{source_file_path}'.format(
                        account_name=settings.AZURE_ACCOUNT_NAME,
                        source_container_name=container_name,
                        source_file_path=blob_name
                    )
            )
        except Exception as exc:
            errors.append('Error creating blob: ' + str(exc))
        errors = delete_blob(
            blob_client=blob_client,
            container_name=container_name,
            blob_name=blob_name,
            errors=errors
        )
    return errors


def delete_blob(blob_client, container_name, blob_name, errors):
    """
    Delete a blob
    """
    # Delete the original blob
    try:
        blob_client.delete_blob(
            container_name,
            blob_name
        )
    except Exception as exc:
        errors.append('Error deleting blob: ' + str(exc))
    return errors


def prep_delete_blob(blob_client, blob_list, container_name):
    """
    Delete blobs based on 'blob_full_path' and handle errors.
    """
    errors = []
    errored_blobs = []
    for blob_dict in blob_list:
        try:
            # blob_full_path is the full path including the blob name
            blob_client.delete_blob(
                container_name,
                blob_dict['blob_full_path']
            )
        except Exception as exc:
            errors.append(exc)
            errored_blobs.append(blob_dict['blob_full_path'])
    if errors:
        # Optionally, log errors or handle them as needed
        pass
    return errors, errored_blobs


def archive(blob_client, blob_list, container_name, container_pk):
    """
    Download blobs to local VM, create ZIP archive, upload to Azure storage,
    and create SAS URL
    """
    try:
        archive_folder = os.path.join('olc_webportalv2', 'media', 'filezone')
        os.makedirs(archive_folder, exist_ok=True)
        # List all the things in the container - if it's a file in reports
        # folder or an assembly, download it.
        blobs = blob_client.list_blobs(container_name=container_name)
        local_files = set()
        for blob_dict in blob_list:
            for blob in blobs:
                if fnmatch.fnmatch(blob.name, blob_dict['blob_full_path']):
                    local_file = os.path.join(
                        archive_folder, os.path.basename(blob.name)
                    )
                    local_files.add(local_file)
                    blob_client.get_blob_to_path(
                        container_name=container_name,
                        blob_name=blob.name,
                        file_path=local_file
                    )
        archive_file = os.path.join(
            archive_folder,
            '{container_name}_{container_pk}.zip'.format(
                container_name=container_name,
                container_pk=container_pk
            )
        )
        with zipfile.ZipFile(archive_file, 'w') as zip_stream:
            for local_file in local_files:
                zip_stream.write(
                    local_file,
                    arcname=os.path.basename(local_file),
                    compress_type=zipfile.ZIP_DEFLATED
                )

        sas_url = generate_archive_download_link(
            blob_client=blob_client,
            container_name='filezone-archives',
            output_zipfile=archive_file,
            expiry=730
        )
        return sas_url
    except Exception as exc:
        return exc


def archive_multiple_containers(
        blob_client,
        blob_list,
        container_list,
        filezone_pk):
    """
    Download blobs to local VM, create ZIP archive, upload to Azure storage,
    and create SAS URLs
    """
    try:
        archive_folder = os.path.join('olc_webportalv2', 'media', 'filezone')
        os.makedirs(archive_folder, exist_ok=True)
        # Create a set to store the names of all the downloaded files to the
        # archive
        local_files = set()
        for container_name in container_list:
            # List all the things in the container - if it's a file in reports
            # folder or an assembly, download it.
            blobs = blob_client.list_blobs(container_name=container_name)
            for blob_dict in blob_list:
                for blob in blobs:
                    if fnmatch.fnmatch(blob.name, blob_dict['blob_name']):
                        local_file = os.path.join(
                            archive_folder, os.path.basename(blob.name)
                        )
                        local_files.add(local_file)
                        blob_client.get_blob_to_path(
                            container_name=container_name,
                            blob_name=blob.name,
                            file_path=local_file
                        )
        archive_file = os.path.join(
            archive_folder, 'filezone_request_{filezone_pk}.zip'.format(
                filezone_pk=filezone_pk
            )
        )
        with zipfile.ZipFile(archive_file, 'w') as zip_stream:
            for local_file in local_files:
                zip_stream.write(
                    local_file,
                    arcname=os.path.basename(local_file),
                    compress_type=zipfile.ZIP_DEFLATED
                )

        sas_url = generate_archive_download_link(
            blob_client=blob_client,
            container_name='filezone-archives',
            output_zipfile=archive_file,
            expiry=730
        )
        return sas_url
    except Exception as exc:
        return exc


@shared_task
def locate_files(
        filezone_pk: int,
        debug=False):
    """
    Run the FileLocate class method to locate containers and files using
    supplied patterns
    :param int filezone_pk: Primary key of the Regexes model to use
    :param bool debug: Boolean for whether debug statements should be printed.
        Default is False
    """
    regexes = Regexes.objects.get(pk=filezone_pk)
    file_obj = FileLocate(
        container_regex=regexes.container_regex_list,
        container_exclude_regex=regexes.container_exclude_regex_list,
        file_regex=regexes.file_regex_list,
        file_exclude_regex=regexes.file_exclude_regex_list,
        debug=debug
    )
    regexes.file_matches, regexes.file_ajax = file_obj.main()
    regexes.status = 'Complete'
    regexes.save()
    write_ajax(
        ajax_dict=regexes.file_ajax,
        filezone_pk=regexes.pk
    )


def write_ajax(
        ajax_dict: dict,
        filezone_pk: int):
    """
    Write the ajax dictionary to file in JSON format
    :param dict ajax_dict: Dictionary of outputs ready for DataTables importing
    :param int filezone_pk: Primary key of the FileZone Regexes request
    """
    # Set the name of the path in which the JSON outputs are to be written
    ajax_path = os.path.join(
        'olc_webportalv2', 'static', 'ajax', 'filezone', str(filezone_pk)
    )
    # Create the folder as required
    os.makedirs(ajax_path, exist_ok=True)
    # Set the name of the file
    json_path = os.path.join(ajax_path, 'arrays.txt')
    # Use the json library to write the dictionary to file
    with open(json_path, 'w', encoding='utf-8') as json_out:
        json.dump(ajax_dict, json_out)
