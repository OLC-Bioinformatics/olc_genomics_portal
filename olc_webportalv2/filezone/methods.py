"""
Methods for FileZone app
"""

import base64
import binascii
import hashlib
import os
import pathlib
import re
from email.utils import parsedate_to_datetime

from olc_webportalv2.common.storage_client import get_storage_client


def human_bytes(byte_count: float) -> str:
    """
    Convert a byte count into a human-readable format with appropriate
    unit (B, KB, MB, GB, TB).

    :param byte_count: The number of bytes as a float.
    :return: A string representing the byte count in a human-friendly format.
    """
    # Suffixes for units
    suffixes = ['B', 'KB', 'MB', 'GB', 'TB']
    index = 0  # Index to track the current unit
    
    # Loop to convert byte_count to a higher unit if applicable
    while byte_count >= 1024 and index < len(suffixes) - 1:
        byte_count /= 1024.0  # Convert to the next higher unit
        index += 1  # Move to the next unit
    
    # Return formatted string with two decimal places and unit suffix
    return "{value:.2f} {suffix}".format(
        value=byte_count, suffix=suffixes[index]
    )


def generate_sas(blob_name, container_name):
    """
    Create a SAS URL for a supplied blob
    """
    return generate_download_link(
        blob_client=get_storage_client(),
        container_name=container_name,
        blob_name=blob_name,
        expiry=7
    )


def generate_download_link(blob_client, container_name, blob_name, expiry=7):
    """
    Generate a download link with Content-Disposition for the blob, good for
    up to expiry days.
    :param blob_client: Provider-neutral Storage client
    :param container_name: Name of the container.
    :param blob_name: Name of the blob to create a link for.
    :param expiry: Number of days the link should be valid for.
    :return: String of a link that allows people to download the blob.
    """
    return blob_client.create_transfer_url(
        container_name,
        'download',
        name=blob_name,
        expires_in_seconds=expiry * 24 * 60 * 60,
        content_disposition='attachment; filename="{0}"'.format(
            blob_name.split('/')[-1]
        ),
    )


def copy_blobs(
        blob_metadata_list: list,
        destination_container: str):
    """
    Copy blobs from one or more containers into a destination container
    :param list blob_metadata_list: List metadata for blobs to be copied
    :param str destination container: Name of the container into which the
    blobs are to be copied
    """
    blob_service = get_storage_client()
    blob_service.ensure_container(destination_container)
    for blob_metadata_dict in blob_metadata_list:
        blob_service.copy_object(
            blob_metadata_dict["container_name"],
            blob_metadata_dict["blob_name"],
            destination_container,
            blob_metadata_dict["blob_name"],
        )


def calculate_checksum(item):
    """
    Calculate an Azure-style checksum for an uploaded file
    :param request.FILES.get item: Current uploaded file
    """
    # Create a hashlib MD5 object to read in the file contents
    md5 = hashlib.md5()
    # Use readlines() to load the file contents
    contents = item.readlines()
    # Iterate over the contents, and update the hashlib object
    for line in contents:
        md5.update(line)
    # Reset the file position to the start
    item.seek(0)
    return base64.b64encode(md5.digest()).decode()


class FileLocate:
    """
    Locate files in blob storage with an optional container regex and a
    file regex
    """

    def main(self):
        """
        Run the container (optional) and file locating methods
        """
        blob_client = FileLocate.create_blob_client()
        container_list = FileLocate.locate_container(
            blob_client=blob_client,
            expressions=self.container_regex,
            negative_expressions=self.container_exclude_regex,
            debug=self.debug
        )
        file_dict, ajax_dict = FileLocate.locate_file(
            blob_client=blob_client,
            containers=container_list,
            expressions=self.file_regex,
            negative_expressions=self.file_exclude_regex,
            debug=self.debug
        )
        return file_dict, ajax_dict

    @staticmethod
    def create_blob_client():
        """
        Create the blob client necessary for the file locating functionality
        """
        return get_storage_client()

    @staticmethod
    def locate_container(
            blob_client,
            expressions: list,
            negative_expressions: list,
            debug: bool):
        """
        Locate containers matching the supplied regex
        :param blob_client: Provider-neutral Storage client
        :param list expressions: List of the regexes to use to search for
            containers
        :param list negative_expressions: List of regexes to use to filter
            returned containers
        :return list containers/container_matches: Matching storage containers
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
                        negative_expression in container.name for
                            negative_expression in negative_expressions):
                        if debug:
                            print(
                                'Container {container_name} matched one of '
                                'the exclusion terms'.format(
                                    container_name=container.name
                                )
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
                    if re.fullmatch(
                            r'{regex_expression}$'.format(
                                regex_expression=regex_expression
                            ),
                            container.name):
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
            expressions: list,
            negative_expressions: list,
            debug: bool):
        """
        Locate files in containers in blob storage with a supplied expression
        :param blob_client: Provider-neutral Storage client
        :param list containers: List of provider-neutral storage containers
        :param list expressions: List of regexes to use to search for files
        :param list negative_expressions: List of regexes to use to filter
            returned files
        :return dict files: Dictionary of expression: container_name: blob_name
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
                                while len(expression_components) < \
                                        len(components):
                                    expression_components.insert(-1, '*')
                                # Reset the number of matches required to the
                                # new length of the
                                # expression components
                                matches_required = len(expression_components)
                                # Use re.sub to convert * to .* to be
                                # consistent with regex rules
                                regex_expression = re.sub(
                                    r'(?<!\.)\*', '.*',
                                    expression_components[i]
                                )
                                # If the components match, increment the
                                # number of matches
                                if re.fullmatch(
                                        r'{regex_expression}$'.format(
                                            regex_expression=regex_expression
                                        ),
                                        component):
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
                                        r'{regex_expression}$'.format(
                                            regex_expression=regex_expression
                                        ),
                                        component):
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
                            negative_expression in filename for
                                negative_expression in negative_expressions):
                            if debug:
                                print(
                                    'File {container_name} / {filename} '
                                    'matched one of the '
                                    'exclusion terms'.format(
                                        container_name=container.name,
                                        filename=filename
                                    ))
                            continue
                    # Add the match to the dictionary
                    if container.name not in files:
                        files[container.name] = []
                    blob_size = human_bytes(
                        byte_count=blob_file.size or 0
                    )
                    sas_url = generate_sas(
                        blob_name=blob_file.name,
                        container_name=container.name
                    )
                    modified = (
                        parsedate_to_datetime(blob_file.last_modified)
                        if blob_file.last_modified else None
                    )
                    blob_date = modified.strftime(
                        "%Y/%m/%d, %H:%M:%S"
                    ) if modified else ''
                    # Extract the MD5 information
                    try:
                        blob_md5 = binascii.hexlify(
                            bytearray(
                                base64.b64decode(
                                    blob_file.content_md5
                                )
                            )
                        ).decode()
                    except (TypeError, AttributeError):
                        blob_md5 = FileLocate.calculate_md5(
                            blob_client=blob_client,
                            blob_file=blob_file,
                            container_name=container.container_name
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
                            'blob_md5': blob_md5
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
                            'blob_md5': blob_md5
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
            container_name: str) -> str:
        """
        Manually calculate the MD5 checksum of a large blob in a
            memory-efficient manner.
        :param blob_client: Provider-neutral Storage client
        :param blob_file: Blob from generator
        :param container_name: Name of container holding blob of interest
        :return: MD5 hex digest of blob
        """
        md5 = hashlib.md5()
        chunk_size = 4096  # Define the chunk size

        # Create a stream to download the blob content
        content = blob_client.download_bytes(container_name, blob_file.name)
        for offset in range(0, len(content), chunk_size):
            md5.update(content[offset:offset + chunk_size])
        return md5.hexdigest()

    def __init__(
            self, container_regex, container_exclude_regex, file_regex,
            file_exclude_regex, debug=False) -> None:
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
            '2018-CAL*.gz'
        ],
        file_exclude_regex=[],
        debug=True
    )
    file_dictionary = file_obj.main()
