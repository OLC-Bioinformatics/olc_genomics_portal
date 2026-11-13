"""Storage utilities and blob search logic for FileZone."""

import base64
import binascii
import hashlib
import os
import pathlib
import re
import tempfile
from email.utils import parsedate_to_datetime
from typing import Dict, List

from olc_webportalv2.common.storage_client import get_storage_client


def human_bytes(byte_count: float) -> str:
    """Convert a byte count to a human-readable unit.

    Args:
        byte_count: Number of bytes.

    Returns:
        The size formatted in bytes, KB, MB, GB, or TB.

    """
    # Suffixes for units
    suffixes = ['B', 'KB', 'MB', 'GB', 'TB']
    index = 0  # Index to track the current unit

    # Loop to convert byte_count to a higher unit if applicable
    while byte_count >= 1024 and index < len(suffixes) - 1:
        byte_count /= 1024.0  # Convert to the next higher unit
        index += 1  # Move to the next unit

    # Return formatted string with two decimal places and unit suffix
    return f'{byte_count:.2f} {suffixes[index]}'


def generate_sas(blob_name, container_name):
    """Create a week-long download URL for a blob.

    Args:
        blob_name: Object path to make downloadable.
        container_name: Storage container holding the object.

    Returns:
        A temporary download URL.

    """
    return generate_download_link(
        blob_client=get_storage_client(),
        container_name=container_name,
        blob_name=blob_name,
        expiry=7,
    )


def generate_download_link(blob_client, container_name, blob_name, expiry=7):
    """Generate an attachment URL for a blob.

    Args:
        blob_client: Provider-neutral storage client.
        container_name: Container holding the blob.
        blob_name: Object path to make downloadable.
        expiry: Number of days the link remains valid.

    Returns:
        A temporary download URL.

    """
    filename = blob_name.split('/')[-1]
    return blob_client.create_transfer_url(
        container_name,
        'download',
        name=blob_name,
        expires_in_seconds=expiry * 24 * 60 * 60,
        content_disposition=f'attachment; filename="{filename}"',
    )


def copy_blobs(
    blob_metadata_list: List[Dict[str, str]],
    destination_container: str,
) -> None:
    """Copy objects to a destination container.

    Args:
        blob_metadata_list: Source container and object path for each blob.
        destination_container: Container receiving the copies.

    """
    blob_service = get_storage_client()
    blob_service.ensure_container(destination_container)
    for blob_metadata_dict in blob_metadata_list:
        blob_service.copy_object(
            blob_metadata_dict['container_name'],
            blob_metadata_dict['blob_name'],
            destination_container,
            blob_metadata_dict['blob_name'],
        )


def calculate_checksum(item):
    """Calculate a base64-encoded MD5 checksum for an uploaded file.

    Args:
        item: Uploaded file-like object supporting read and seek.

    Returns:
        Base64-encoded MD5 digest.

    """
    md5 = hashlib.md5()
    for chunk in iter(lambda: item.read(1024 * 1024), b''):
        md5.update(chunk)
    item.seek(0)
    return base64.b64encode(md5.digest()).decode()


class FileLocate:
    """Locate blobs by container and object-name patterns."""

    def main(self):
        """Find matching blobs and return page and AJAX data.

        Returns:
            A tuple of per-container file data and DataTables rows.

        """
        blob_client = FileLocate.create_blob_client()
        container_list = FileLocate.locate_container(
            blob_client=blob_client,
            expressions=self.container_regex,
            negative_expressions=self.container_exclude_regex,
            debug=self.debug,
        )
        file_dict, ajax_dict = FileLocate.locate_file(
            blob_client=blob_client,
            containers=container_list,
            expressions=self.file_regex,
            negative_expressions=self.file_exclude_regex,
            debug=self.debug,
        )
        return file_dict, ajax_dict

    @staticmethod
    def create_blob_client():
        """Return the shared provider-neutral storage client."""
        return get_storage_client()

    @staticmethod
    def locate_container(
        blob_client,
        expressions: List[str],
        negative_expressions: List[str],
        debug: bool,
    ):
        """Return containers matching the supplied patterns.

        Args:
            blob_client: Provider-neutral storage client.
            expressions: Patterns used to select containers.
            negative_expressions: Terms that exclude matching containers.
            debug: Whether to print search details.

        Returns:
            Matching storage container records.

        """
        # Get a list of all the containers
        containers = blob_client.list_containers()

        # If no container filtering information was provided, return all the
        # containers
        if not expressions:
            return containers

        # Prepare a list to store the containers that match the expression
        container_matches = []

        # Iterate over the list of regexes
        for expression in expressions:
            # Filter the containers with the current regex
            for container in containers:
                # Reject if the match contains any of the exclusion terms
                if negative_expressions != ['']:
                    if any(
                        negative_expression in container.name
                        for negative_expression in negative_expressions
                    ):
                        if debug:
                            print(
                                f'Container {container.name} matched one of '
                                'the exclusion terms'
                            )
                        continue
                # If the expression contains non-alphanumeric characters
                # either at the start or
                # anywhere, treat it as a regular expression
                if re.match(r'.*\W', expression.replace('-', '_')):
                    # Use re.sub to convert * to .* to be consistent with
                    # regex rules, as it seemed unintuitive to force the user
                    # to use .* rather than just * for simple queries.
                    # If .* was provided, don't add the '.' by using a
                    # negative lookbehind assertion
                    regex_expression = re.sub(r'(?<!\.)\*', '.*', expression)
                    # Use re.fullmatch to determine if the expression matches
                    # the container name
                    if re.fullmatch(rf'{regex_expression}$', container.name):
                        # Update the match boolean and append the container to
                        # the list of matches
                        container_matches.append(container)
                # The expression doesn't appear to be a regular expression
                else:
                    # Ensure a perfect match for non regex queries
                    if expression == container.name:
                        # Update the match boolean and append the container to
                        # the list of matches
                        container_matches.append(container)
        if debug:
            print('Container(s) matching supplied pattern(s)')
            for container_match in container_matches:
                print(container_match.name)
        return container_matches

    @staticmethod
    def locate_file(
        blob_client,
        containers: list,
        expressions: List[str],
        negative_expressions: List[str],
        debug: bool,
    ):
        """Find matching blobs and build the page and AJAX results.

        Args:
            blob_client: Provider-neutral storage client.
            containers: Storage containers to search.
            expressions: Patterns used to select object paths.
            negative_expressions: Terms that exclude matching object paths.
            debug: Whether to print search details.

        Returns:
            A tuple containing per-container file data and DataTables rows.

        """
        # Initialise a dictionary to store the matches
        files = {}
        ajax = {}
        ajax['data'] = []
        # Initialise a counter, so each match has a unique primary key
        count = 0

        # List all the files in each of the containers that match the provided
        # expression
        for container in containers:
            blobs = blob_client.list_objects(container.name)
            # Iterate through all the files in the container
            for blob_file in blobs:
                # Store the file name and path in a variable
                filename = blob_file.name
                # Initialise a variable to track whether this file is a match
                # to the expression
                match = False
                # Use pathlib to create a path object from the file name
                path_obj = pathlib.Path(os.path.normpath(filename))
                # Split the file name into its separate components
                components = path_obj.parts
                for expression in expressions:
                    # Check whether the expression contains non-alphanumeric
                    # characters. If it does, treat it as a regular expression.
                    # Ignore dashes as non-alphanumeric characters
                    if re.match(r'.*\W', expression.replace('-', '_')):
                        # If the expression is has nested files/folders, split
                        # the expression
                        # into its components
                        # e.g. reports/outputs/output.tsv contains three
                        # components
                        expression_obj = pathlib.Path(
                            os.path.normpath(expression)
                        )
                        expression_components = list(expression_obj.parts)
                        # The number of matches required is the number of path
                        # components e.g. reports/outputs/output.tsv requires
                        # three matches
                        matches_required = len(expression_components)
                        # Initialise a dictionary to track matches to each of
                        # the components
                        component_matches = {}
                        # Search through all the path components of the file
                        # name
                        for i, component in enumerate(components):
                            # Check for nested files/folders
                            if len(expression_components) > 1:
                                while len(expression_components) < len(
                                    components
                                ):
                                    expression_components.insert(-1, '*')
                                # Reset the number of matches required to the
                                # new length of the
                                # expression components
                                matches_required = len(expression_components)
                                # Use re.sub to convert * to .* to be
                                # consistent with regex rules
                                regex_expression = re.sub(
                                    r'(?<!\.)\*',
                                    '.*',
                                    expression_components[i],
                                )
                                # If the components match, increment the
                                # number of matches
                                if re.fullmatch(
                                    rf'{regex_expression}$', component
                                ):
                                    # Set the match to the current component
                                    # to true
                                    component_matches[component] = True
                            else:
                                # Use re.sub to convert * to .* to be
                                # consistent with regex rules
                                regex_expression = re.sub(
                                    r'(?<!\.)\*', '.*', expression
                                )
                                # If the component matches, set the match
                                # boolean to True
                                if re.fullmatch(
                                    rf'{regex_expression}$', component
                                ):
                                    match = True
                        # Check to see if the number of matches observed in a
                        # multi-component expression is the number matches
                        # required for a match before setting the match
                        # boolean to True
                        if len(component_matches) == matches_required:
                            match = True
                    # The expression does not look like a regular expression
                    else:
                        for component in components:
                            # An exact match is required to be considered a
                            # match
                            if expression == component:
                                match = True
                # Update dictionaries with a successful match
                if match:
                    # Reject if the match contains any of the exclusion terms
                    if negative_expressions != ['']:
                        if any(
                            negative_expression in filename
                            for negative_expression in negative_expressions
                        ):
                            if debug:
                                print(
                                    f'File {container.name} / {filename} '
                                    'matched one of the '
                                    'exclusion terms'
                                )
                            continue
                    # Add the match to the dictionary
                    if container.name not in files:
                        files[container.name] = []
                    blob_size = human_bytes(byte_count=blob_file.size or 0)
                    sas_url = generate_sas(
                        blob_name=blob_file.name, container_name=container.name
                    )
                    modified = (
                        parsedate_to_datetime(blob_file.last_modified)
                        if blob_file.last_modified
                        else None
                    )
                    blob_date = (
                        modified.strftime('%Y/%m/%d, %H:%M:%S')
                        if modified
                        else ''
                    )
                    # Extract the MD5 information
                    try:
                        blob_md5 = binascii.hexlify(
                            bytearray(base64.b64decode(blob_file.content_md5))
                        ).decode()
                    except (TypeError, AttributeError):
                        blob_md5 = FileLocate.calculate_md5(
                            blob_client=blob_client,
                            blob_file=blob_file,
                            container_name=container.container_name,
                        )
                    # Update the dictionaries with the metadata
                    files[container.name].append(
                        {
                            'pk': count,
                            'container_name': container.name,
                            'blob_name': filename,
                            'blob_size': blob_size,
                            'blob_date': blob_date,
                            'blob_download_link': sas_url,
                            'blob_md5': blob_md5,
                        }
                    )
                    ajax['data'].append(
                        {
                            'container_name': container.name,
                            'pk': count,
                            'blob_name': filename,
                            'blob_size': blob_size,
                            'blob_date': blob_date,
                            'blob_download_link': sas_url,
                            'blob_md5': blob_md5,
                        }
                    )
                    count += 1
        if debug:
            if files:
                print('File(s) matching supplied pattern(s)')
                for container_name, file_list in files.items():
                    print('\t', container_name)
                    for blob_dict in file_list:
                        print('\t\t', blob_dict)
            else:
                print('No files matched supplied pattern(s)')
        return files, ajax

    @staticmethod
    def calculate_md5(
        blob_client,
        blob_file,
        container_name: str,
    ) -> str:
        """Calculate a blob's MD5 digest without loading it into memory.

        Args:
            blob_client: Provider-neutral storage client.
            blob_file: Storage object metadata containing its name.
            container_name: Container holding the blob.

        Returns:
            The blob's MD5 digest as a hexadecimal string.

        """
        md5 = hashlib.md5()
        with tempfile.TemporaryDirectory(prefix='filezone-md5-') as temp_dir:
            local_path = os.path.join(temp_dir, 'blob')
            blob_client.download_file(
                container_name,
                blob_file.name,
                local_path,
            )
            with open(local_path, 'rb') as blob_stream:
                for chunk in iter(
                    lambda: blob_stream.read(1024 * 1024),
                    b'',
                ):
                    md5.update(chunk)
        return md5.hexdigest()

    def __init__(
        self,
        container_regex: List[str],
        container_exclude_regex: List[str],
        file_regex: List[str],
        file_exclude_regex: List[str],
        debug: bool = False,
    ) -> None:
        """Store the patterns used for a blob search.

        Args:
            container_regex: Patterns selecting storage containers.
            container_exclude_regex: Terms excluding containers.
            file_regex: Patterns selecting object paths.
            file_exclude_regex: Terms excluding object paths.
            debug: Whether to print search details.

        """
        self.container_regex = container_regex
        self.container_exclude_regex = container_exclude_regex
        self.file_regex = file_regex
        self.file_exclude_regex = file_exclude_regex
        self.debug = debug


if __name__ == '__main__':
    file_obj = FileLocate(
        container_regex=['000*', '*fake'],
        container_exclude_regex=[],
        file_regex=[
            '2017-SEQ-0393*',
            'Best*/2018-CAL*.fasta',
            'InterOp/Cont*',
            '2018-CAL*.gz',
        ],
        file_exclude_regex=[],
        debug=True,
    )
    file_dictionary = file_obj.main()
