"""Tasks and storage helpers for the FileZone app."""

# Standard imports
import base64
import binascii
import json
import os
import pathlib
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from typing import List, Tuple

# Third-party imports
from celery import shared_task
from django.db import DatabaseError
from sentry_sdk import capture_exception

# Local imports
from olc_webportalv2.common.storage_client import (
    StorageServiceError,
    get_storage_client,
)
from olc_webportalv2.cowbat.tasks import (
    generate_download_link as generate_archive_download_link,
)
from olc_webportalv2.filezone.methods import (
    FileLocate,
    generate_sas,
    human_bytes,
)
from olc_webportalv2.filezone.models import ContainerName, Regexes


def find_containers() -> List[str]:
    """Return sorted container names from the storage service."""
    containers = get_storage_client().list_containers()
    return sorted({str(container.name) for container in containers})


@shared_task
def refresh_container_names():
    """Refresh stored container names from the storage service."""
    try:
        # Autocompletion: clear out all the previous data
        ContainerName.objects.all().delete()
        # Find all the blobs that match the naming format
        container_list = find_containers()
        # Add the blobs to the model
        ContainerName.objects.bulk_create(
            ContainerName(container_name=container)
            for container in container_list
        )
    except (StorageServiceError, DatabaseError) as exc:
        capture_exception(exc)


def create_container(container_name):
    """Create a container in storage, propagating service errors.

    Args:
        container_name: Name of the container to create.

    """
    get_storage_client().ensure_container(container_name)


def calculate_md5_parallel(blob_client, blob, container_name):
    """Calculate a blob's MD5 digest.

    Use the provider-supplied checksum when available and fall back to a
    streamed calculation when it is missing.

    Args:
        blob_client: Provider-neutral storage client.
        blob: Storage object metadata containing its name and checksum.
        container_name: Container holding the blob.

    Returns:
        The MD5 digest as a hexadecimal string.

    """
    try:
        # Attempt to use the MD5 provided by Azure
        return binascii.hexlify(
            bytearray(base64.b64decode(blob.content_md5))
        ).decode()
    except (TypeError, AttributeError):
        return FileLocate.calculate_md5(
            blob_client=blob_client,
            blob_file=blob,
            container_name=container_name,
        )


def list_blobs(filezone_pk):
    """List non-empty blobs and their metadata.

    Calculate missing MD5 values in parallel for the selected container.

    Args:
        filezone_pk: Primary key of the container database record.

    Returns:
        Blob hierarchy and a mapping from row IDs to blob metadata.

    """
    blob_client = get_storage_client()
    # Retrieve container name using the FileZone primary key
    container = ContainerName.objects.get(pk=filezone_pk)

    # List all blobs in the specified container
    blobs = blob_client.list_objects(container.container_name)

    # Filter out zero-size blobs and sort by blob name
    blobs = sorted(
        [blob for blob in blobs if (blob.size or 0) > 0],
        key=lambda blob: blob.name,
    )

    blob_data = []
    pk_to_blob_dict = {}  # Mapping of pk to blob dictionary

    # Pre-assign pks based on sorted order
    for index, blob in enumerate(blobs, start=1):
        file_name = blob.name.split('/')[-1]
        blob_dict = {
            'pk': index,
            'container_name': container.container_name,
            'blob_name': file_name,
            'blob_full_path': blob.name,
            # Placeholder values, will be updated after MD5 calculation
            'blob_size': None,
            'blob_date': None,
            'blob_download_link': None,
            'blob_md5': None,
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
                container.container_name,
            ): index
            for index, blob in enumerate(blobs, start=1)
        }

        for future in as_completed(future_to_blob_index):
            index = future_to_blob_index[future]
            blob_md5 = future.result()
            blob = blobs[index - 1]  # Adjust index to 0-based for list access

            # Update blob details with results from parallel processing
            blob_data[index - 1].update(
                {
                    'blob_size': human_bytes(byte_count=blob.size or 0),
                    'blob_date': (
                        parsedate_to_datetime(blob.last_modified).strftime(
                            '%Y/%m/%d, %H:%M:%S'
                        )
                        if blob.last_modified
                        else ''
                    ),
                    'blob_download_link': generate_sas(
                        blob_name=blob.name,
                        container_name=container.container_name,
                    ),
                    'blob_md5': blob_md5,
                }
            )

    # Calculate blob hierarchy
    blob_hierarchy = organize_blobs_into_hierarchy(blob_data)

    return blob_hierarchy, pk_to_blob_dict


def organize_blobs_into_hierarchy(blob_data):
    """Organize flat blob data into a path-based hierarchy.

    Args:
        blob_data: Blob dictionaries containing full object paths.

    Returns:
        Nested dictionaries representing folders and files.

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
    """Return characters that are not allowed in a new blob name.

    Args:
        blob_rename: User-supplied destination object path.

    Returns:
        The set of disallowed characters present in the new name.

    """
    invalid_chars = '^@!#&<>{}[]\\~`]*$\'"%'
    return set(invalid_chars).intersection(blob_rename)


def rename_blob(blob_name, blob_rename, container_name):
    """Copy a blob to its new name and delete the original.

    Args:
        blob_name: Current object path.
        blob_rename: Destination object path.
        container_name: Container holding the blob.

    Returns:
        Storage errors encountered while copying or deleting.

    """
    errors = []
    blob_client = get_storage_client()
    try:
        blob_client.copy_object(
            container_name, blob_name, container_name, blob_rename
        )
    except StorageServiceError as exc:
        return ['Error creating blob: ' + str(exc)]
    return delete_blob(
        blob_client=blob_client,
        container_name=container_name,
        blob_name=blob_name,
        errors=errors,
    )


def delete_blob(blob_client, container_name, blob_name, errors):
    """Delete a blob and append any storage error.

    Args:
        blob_client: Provider-neutral storage client.
        container_name: Container holding the blob.
        blob_name: Object path to delete.
        errors: Existing error messages to update.

    Returns:
        The error list, including any deletion error.

    """
    # Delete the original blob
    try:
        blob_client.delete_object(container_name, blob_name)
    except StorageServiceError as exc:
        errors.append('Error deleting blob: ' + str(exc))
    return errors


def prep_delete_blob(blob_client, blob_list, container_name):
    """Delete selected blob paths while collecting individual failures.

    Args:
        blob_client: Provider-neutral storage client.
        blob_list: Selected objects containing ``blob_full_path``.
        container_name: Container holding the objects.

    Returns:
        Storage errors and the paths that could not be deleted.

    """
    errors = []
    errored_blobs = []
    for blob_dict in blob_list:
        try:
            # blob_full_path is the full path including the blob name
            blob_client.delete_object(
                container_name, blob_dict['blob_full_path']
            )
        except StorageServiceError as exc:
            errors.append(exc)
            errored_blobs.append(blob_dict['blob_full_path'])
    return errors, errored_blobs


def archive(blob_client, blob_list, container_name, container_pk):
    """Archive selected objects from one container and return a download URL.

    Args:
        blob_client: Provider-neutral storage client.
        blob_list: Selected objects, each containing ``blob_full_path``.
        container_name: Container holding the selected objects.
        container_pk: Container database primary key for the archive name.

    Returns:
        A temporary download URL for the uploaded archive.

    """
    blob_paths = [
        (container_name, blob_dict['blob_full_path'])
        for blob_dict in blob_list
    ]
    return _create_archive(
        blob_client=blob_client,
        blob_paths=blob_paths,
        archive_name=f'container_{container_pk}.zip',
        include_container=False,
    )


def archive_multiple_containers(
    blob_client,
    blob_list,
    filezone_pk,
):
    """Archive selected objects from multiple containers.

    Args:
        blob_client: Provider-neutral storage client.
        blob_list: Selected objects with ``container_name`` and ``blob_name``.
        filezone_pk: FileZone request primary key for the archive name.

    Returns:
        A temporary download URL for the uploaded archive.

    """
    blob_paths = [
        (blob_dict['container_name'], blob_dict['blob_name'])
        for blob_dict in blob_list
    ]
    return _create_archive(
        blob_client=blob_client,
        blob_paths=blob_paths,
        archive_name=f'filezone_request_{filezone_pk}.zip',
        include_container=True,
    )


def _create_archive(
    blob_client,
    blob_paths: List[Tuple[str, str]],
    archive_name: str,
    include_container: bool,
) -> str:
    """Download selected objects, archive them, and upload the ZIP.

    Args:
        blob_client: Provider-neutral storage client.
        blob_paths: Container and object-name pairs to include.
        archive_name: Local filename for the generated ZIP.
        include_container: Whether to prefix members with their container.

    Returns:
        A temporary download URL for the uploaded archive.

    """
    with tempfile.TemporaryDirectory(prefix='filezone-') as archive_folder:
        archive_file = os.path.join(archive_folder, archive_name)
        with zipfile.ZipFile(
            archive_file,
            'w',
            compression=zipfile.ZIP_DEFLATED,
        ) as zip_stream:
            for index, (container_name, blob_name) in enumerate(blob_paths):
                local_file = os.path.join(
                    archive_folder,
                    f'download-{index}',
                )
                blob_client.download_file(
                    container_name,
                    blob_name,
                    local_file,
                )
                archive_path = _archive_member_name(
                    container_name,
                    blob_name,
                    include_container,
                )
                zip_stream.write(local_file, arcname=archive_path)

        return generate_archive_download_link(
            blob_service_client=blob_client,
            container_name='filezone-archives',
            output_zipfile=archive_file,
        )


def _archive_member_name(
    container_name: str,
    blob_name: str,
    include_container: bool,
) -> str:
    """Build a relative ZIP member path without traversal components.

    Args:
        container_name: Container holding the object.
        blob_name: Object path to include in the archive.
        include_container: Whether to prefix the path with the container.

    Returns:
        A normalized relative path for the ZIP member.

    """
    path_parts = [
        part
        for part in pathlib.PurePosixPath(blob_name.replace('\\', '/')).parts
        if part not in {'/', '.', '..'}
    ]
    if not path_parts:
        path_parts = ['unnamed-blob']
    if include_container:
        container_part = pathlib.PurePosixPath(container_name).name
        if container_part in {'', '.', '..'}:
            container_part = 'container'
        path_parts.insert(0, container_part)
    return pathlib.PurePosixPath(*path_parts).as_posix()


@shared_task
def locate_files(filezone_pk: int, debug=False):
    """Locate matching containers and files for a saved search.

    Args:
        filezone_pk: Primary key of the Regexes search request.
        debug: Whether to print search details.

    """
    regexes = Regexes.objects.get(pk=filezone_pk)
    file_patterns = regexes.file_regex_list or []
    if not file_patterns:
        regexes.status = 'Failed'
        regexes.save(update_fields=['status'])
        raise ValueError(
            f'FileZone request {filezone_pk} has no file search patterns.'
        )

    file_obj = FileLocate(
        container_regex=regexes.container_regex_list or [],
        container_exclude_regex=(regexes.container_exclude_regex_list or ['']),
        file_regex=file_patterns,
        file_exclude_regex=regexes.file_exclude_regex_list or [''],
        debug=debug,
    )
    file_matches, file_ajax = file_obj.main()
    regexes.file_matches = file_matches
    regexes.file_ajax = file_ajax
    regexes.status = 'Complete'
    regexes.save()
    write_ajax(ajax_dict=file_ajax, filezone_pk=regexes.pk)


def write_ajax(ajax_dict: dict, filezone_pk: int):
    """Write search results to the FileZone AJAX data file.

    Args:
        ajax_dict: DataTables-compatible search results.
        filezone_pk: Primary key of the Regexes search request.

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
