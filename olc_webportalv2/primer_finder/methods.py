#!/usr/bin/env python

"""
Methods for the primer_finder app
"""

# Standard imports
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import os
import shutil
import smtplib
import tempfile
from time import sleep
from typing import (
    Any,
    Dict,
    Tuple,
    Type,
    Union
)

# Django-related imports
from django.conf import settings  # To access azure credentials
from django.db.models.query import QuerySet


# Third-party imports
from azure.storage.blob import BlockBlobService
from azure.storage.blob import ContentSettings
from bulk_update.helper import bulk_update
import xlsxwriter

# Portal-specific imports
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    VerifierPrimerSet,
    VerifierPrimers,
    VerifierPanel,
    VerifierSEQID,
    ValidatorPanel,
    ValidatorPrimers,
    ValidatorPrimerSet,
    ValidatorRequest,
    ValidatorSEQID
)
from olc_webportalv2.cowbat.methods import AzureBatch


def upload_primers(
    *,  # Enforce keyword arguments
    request: Any
):
    """
    Create a local folder to store the batch config file, and upload the
    primer sequence to blob storage

    Args:
        request: The request object containing primer information.
    """
    # Set the container name appropriately
    container_name = request.container_namer()
    run_folder = os.path.join(
        'olc_webportalv2',
        'media',
        '{container_name}'.format(container_name=container_name)
    )

    # Create the run folder as necessary
    os.makedirs(run_folder, exist_ok=True)

    # The primer sequences are stored in a generic file name
    file_name = 'primers.fasta'

    # Create a blob service client to manipulate blobs
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY)

    # Create the container
    blob_client.create_container(container_name)

    # Create a string of the primer sequences. Only PrimerVerifierRequests
    # have the .primer_sequences attribute
    try:
        primer_sequences = request.primer_sequences
    except AttributeError:
        primer_sequences = '{forward_primer}\r\n{reverse_primer}\r\n'\
            .format(forward_primer=request.forward_primer,
                    reverse_primer=request.reverse_primer)

    # Write the bytes-encoded primer sequences to blob storage
    blob_client.create_blob_from_bytes(
        container_name=container_name,
        blob_name=file_name,
        blob=primer_sequences.encode('utf-8'))

    return container_name, file_name


def upload_probe(request: ValidatorRequest):
    """
    Upload the probe sequence to blob storage

    Parameters
        request: ValidatorRequest object
    """
    # The probe sequence is stored with a generic file name
    file_name = 'probe.fasta'

    # Create a blob service client to manipulate blobs
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )

    # Set the container name appropriately
    container_name = request.container_namer()

    # Add a header to the probe sequence
    probe_sequence = '>probe\r\n{sequence}'.format(
        sequence=request.probe_sequence
    )

    # Write the bytes-encoded primer sequences to blob storage
    blob_client.create_blob_from_bytes(
        container_name=container_name,
        blob_name=file_name,
        blob=probe_sequence.encode('utf-8')
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
            server.quit()


def populate_primer_sets(
    *,  # Ensure that the following arguments are keyword arguments
    pk: int,
    primer_list: list,
    model: Type[Union[VerifierPrimerSet, ValidatorPrimerSet]] =
        VerifierPrimerSet
):
    """
    Populate the database using the PrimerSet model.

    Args:
        pk (int): Primary key of the PrimerVerifierRequest.
        primer_list (list): List of all primer base names (e.g. LinA-F +
            LinA-R -> LinA).
        model (models.Model): PrimerSet model to populate. Default is
            VerifierPrimerSet.
    """
    id_string = 'verifier_request_id' if model == VerifierPrimerSet \
        else 'validator_request_id'

    # Fetch existing primer sets in a single query
    existing_primers = model.objects.filter(**{id_string: pk}).values_list(
        'primer_name', flat=True
    )

    # Convert to a set for faster lookups
    existing_primers_set = set(existing_primers)

    # List to hold new PrimerSet objects to be created
    new_primer_sets = []

    for primer in primer_list:
        if primer not in existing_primers_set:
            # Create a new PrimerSet object
            new_primer_sets.append(
                model(**{id_string: pk, 'primer_name': primer})
            )

    # Bulk create new PrimerSet objects
    if new_primer_sets:
        model.objects.bulk_create(new_primer_sets)


def populate_primer_details(
    *,  # Ensure that the following arguments are keyword arguments
    details: dict,
    query: QuerySet,
    model: Type[Union[VerifierPrimers, ValidatorPrimers]] = VerifierPrimers
):
    """
    Populate the database using the Primers model.

    Args:
        details (dict): Dictionary of primer base name: primer name: sequence
            e.g. LinA: { 'LinA-F': 'GAACGA...', 'LinA-R': 'TTTGAT...' },
            LinB: { 'LinB-F'.... }.
        query (QuerySet): PrimerSet.objects.filter(
            verifier_request_id=PrimerVerifierRequest_primary_key). The query
            returns a list of the primer base names (e.g. ['LinA', 'LinB']).
        model (models.Model): PrimerSet model to populate. Default is
            VerifierPrimers.
    """
    # Prepare a list to hold new primer objects
    new_primer_objects = []

    # Prepare a set of existing primer details for quick lookups
    existing_primer_details = set(
        model.objects.filter(primer_id__in=query)
        .values_list('primer_id', 'primer_header')
    )

    # Iterate through all the primer sets
    for primer_set in query:
        # Extract the forward and reverse primer dictionary from the details
        # dictionary using the primer base name
        primers = details[primer_set.primer_name]

        # Iterate through the primer name and sequence extracted from the
        # primer dictionary
        for primer_name, primer_sequence in primers.items():
            # Check if the primer detail already exists
            if (primer_set.pk, primer_name) not in existing_primer_details:
                # Create a new primer object
                new_primer_objects.append(
                    model(primer_id=primer_set.pk, primer_header=primer_name,
                          primer_sequence=primer_sequence)
                )

    # Use bulk_create to efficiently add all new primer objects
    model.objects.bulk_create(new_primer_objects)


def populate_panel(
    *,  # Ensure that the following arguments are keyword arguments
    genera: list,
    panel_type: str,
    pk: int,
    panel: Type[Union[VerifierPanel, ValidatorPanel]] = VerifierPanel
):
    """
    Populate the database using the supplied panel.

    Args:
        genera (list): List of genera in the appropriate panel (e.g.
            ['escherichia', 'listeria']).
        panel_type (str): String of the panel type. Options are 'inclusivity'
            and 'exclusivity'.
        pk (int): Primary key of the PrimerVerifierRequest.
        panel (models.Model): Panel model to populate. Default is
            VerifierPanel.
    """
    id_string = 'verifier_request_id' if panel == VerifierPanel else \
        'validator_request_id'

    # Fetch existing panels in a single query
    existing_panels = panel.objects.filter(
        **{id_string: pk, 'panel': panel_type}
    ).values_list('genus', flat=True)

    # Convert to a set for faster lookups
    existing_panels_set = set(existing_panels)

    # List to hold new Panel objects to be created
    new_panel_objects = []

    for genus in genera:
        genus = genus.capitalize()
        if genus not in existing_panels_set:
            # Append the new Panel object to the list
            new_panel_objects.append(
                panel(**{id_string: pk, 'genus': genus, 'panel': panel_type})
            )

    # Bulk create new Panel objects
    if new_panel_objects:
        panel.objects.bulk_create(new_panel_objects)


def retrieve_panel_seqids(
    genus: str,
    run_folder: str,
    seq_dictionary: dict,
):
    """
    Find all the SEQIDs associated with each genus-specific validation panel

    :param genus: type str: Genus of the panel
    :param run_folder: type str: Path to the folder containing the downloaded
        run files
    :param seq_dictionary: type dict: Dictionary of genus: list of SEQIDs

    :return: seq_dictionary: Dictionary of genus: list of SEQIDs
    """
    # Construct the path to the seqids.txt file
    seqids_file = os.path.join(
        run_folder, "{genus}_seqids.txt".format(genus=genus)
    )

    # Check if the file exists
    if not os.path.exists(seqids_file):
        print("Warning: {seqids} not found.".format(seqids=seqids_file))
        return seq_dictionary

    # Read the SEQIDs from the file
    with open(seqids_file, "r", encoding='utf-8') as f:
        seqids = [line.strip() for line in f]

    # Initialise the genus key in the dictionaries as required
    if genus not in seq_dictionary:
        seq_dictionary[genus] = []

    # Iterate through the SEQIDs
    for seqid in seqids:
        # The benchmark datasets have an issue (right now) where the SEQIDs
        # are not consistent. This is a temporary fix to ensure that the
        # SEQIDs are consistent
        if 'ge,omic' in seqid:
            seqid = seqid.replace('ge,omic', 'genomic')
        # Append the SEQID to the list
        seq_dictionary[genus].append(seqid)

    return seq_dictionary


def populate_database(
    *,  # Ensure that the following arguments are keyword arguments
    run_folder: str,
    verifier_request_pk: int
):
    """
    Populate the database with the details from the PrimerVerifierRequest
    :param run_folder: type str: Path to the folder containing the downloaded
        run files
    :param verifier_request_pk: type int: Primary key of the
    PrimerVerifierRequest
    """
    # Create query sets of panel objects corresponding to the
    # PrimerVerifierRequest primary key
    panel_query = VerifierPanel.objects.filter(
        verifier_request_id=verifier_request_pk
    )

    # Initialise a dictionary to store genus: list of SEQIDs
    seq_dict = {}

    # Populate a dictionary of genus: [SEQIDs] from the Azure storage
    # containers
    for panel in panel_query:
        seq_dictionary = retrieve_panel_seqids(
            genus=panel.genus.lower(),
            run_folder=run_folder,
            seq_dictionary=seq_dict,
        )

        # Update the dictionary with the new values
        seq_dict.update(seq_dictionary)


def populate_report(
    *,  # Ensure that the following arguments are keyword arguments
    request: Type[Union[PrimerVerifierRequest, ValidatorRequest]]
) -> Tuple[Union[PrimerVerifierRequest, ValidatorRequest], str]:
    """
    Download the JSON-formatted report from blob storage, parse it, and store
    the contents in the PrimerVerifierRequest model.

    Args:
        request (PrimerVerifierRequest): PrimerVerifierRequest object.

    Returns:
        Tuple[PrimerVerifierRequest, str]: Updated request and path to the
            report folder.
    """
    try:
        container_name = request.container_namer()
        run_folder = os.path.join(
            'olc_webportalv2',
            'media',
            '{container_name}'.format(container_name=container_name)
        )
        report_folder = os.path.join(run_folder, 'reports')

        if not os.path.isdir(report_folder) and not request.report:
            _download_reports(
                container_name=container_name,
                run_folder=run_folder
            )

        if not request.report:
            request = _load_and_store_report(
                report_folder=report_folder,
                request=request
            )

        return request, report_folder

    except Exception as exc:
        print(
            'Error in populate_report for %s: %s',
            request.container_namer(),
            str(exc),
        )
        raise


def _download_reports(
    *,  # Ensure that the following arguments are keyword arguments
    container_name: str,
    run_folder: str
):
    """
    Download the reports from Azure Blob Storage.

    Args:
        container_name (str): The name of the container.
        run_folder (str): The path to the run folder.
    """
    # Create a blob service client to manipulate blobs
    blob_service = BlockBlobService(
        account_key=settings.AZURE_ACCOUNT_KEY,
        account_name=settings.AZURE_ACCOUNT_NAME
    )

    # Download the container
    AzureBatch.download_container(
        blob_service=blob_service,
        container_name=container_name,
        output_dir=run_folder
    )


def _load_and_store_report(
    *,  # Ensure that the following arguments are keyword arguments
    report_folder: str,
    request: PrimerVerifierRequest
) -> PrimerVerifierRequest:
    """
    Load the JSON report and store it in the PrimerVerifierRequest model.

    Args:
        report_folder (str): The path to the reports folder.
        request (PrimerVerifierRequest): The request object.

    Returns:
        PrimerVerifierRequest: The updated request object.
    """
    # Set the path to the JSON report file
    json_report_file = os.path.join(
        report_folder,
        'inclusivity_exclusivity_report.json'
    )

    # Load the JSON report
    with open(json_report_file, 'r', encoding='utf-8') as report_object:

        # Use json.load to load the JSON report
        json_report = json.load(report_object)

        # Store the JSON report in the request object
        request.report = json.dumps(
            json_report,
            sort_keys=True,
            indent=4,
            separators=(',', ': ')
        )
        request.save()
    return request


def retrieve_sets(
    request: Union[PrimerVerifierRequest, ValidatorRequest],
    primer_set_model: Union[VerifierPrimerSet, ValidatorPrimerSet],
    panel_model: Union[VerifierPanel, ValidatorPanel],
    analysis: str
):
    """
    Extract the primer and panel query sets from models using the primary key
    of a request

    :param request: type django.db.models.Model: PrimerVerifierRequest or
    ValidatorRequest filtered by primary key of current request
    :param primer_set_model: type django.db.models.Model: VerifierPrimerSet or
    ValidatorPrimerSet
    :param panel_model: django.db.models.Model: VerifierPanel or ValidatorPanel
    :param analysis: type str: String of the current analysis type.

    :return: report: JSON-formatted PrimerValidator report
    :return: primer_set: Query set of objects from primer_set_model
    corresponding to request.pk
    :return: panel_set: Query set of objects from panel_model
    corresponding to request.pk
    """
    # Retrieve the report from the request. Convert to a dictionary with json
    report = json.loads(request.report)
    id_string = str()
    if analysis == 'verifier':
        id_string = 'verifier_request_id'
    elif analysis == 'validator':
        id_string = 'validator_request_id'
    else:
        pass
    keyword_dict = {
        id_string: request.pk
    }
    # Retrieve the PrimerSet object(s) corresponding to the request primary key
    primer_set = primer_set_model.objects.filter(**keyword_dict)
    # Retrieve the Panel object(s) corresponding to the request primary key
    panel_set = panel_model.objects.filter(**keyword_dict)
    return report, primer_set, panel_set


def populate_sequence_details(
    *,  # Ensure that the following arguments are keyword arguments
    request: Union[PrimerVerifierRequest, ValidatorRequest],
    primer_set_model: Union[VerifierPrimerSet, ValidatorPrimerSet],
    panel_model: Union[VerifierPanel, ValidatorPanel],
    seqid_model: Union[VerifierSEQID, ValidatorSEQID],
    analysis: str = 'verifier'
) -> Union[PrimerVerifierRequest, ValidatorRequest]:
    """
    Parse the JSON-formatted report and populate the SEQID model with
    sequence-specific information.

    Args:
        request (Union[PrimerVerifierRequest, ValidatorRequest]): Request
            object.
        primer_set_model (Union[VerifierPrimerSet, ValidatorPrimerSet]):
            PrimerSet model.
        panel_model (Union[VerifierPanel, ValidatorPanel]): Panel model.
        seqid_model (Union[VerifierSEQID, ValidatorSEQID]): SEQID model.
        analysis (str): Analysis type ('verifier' or 'validator').

    Returns:
        Union[PrimerVerifierRequest, ValidatorRequest]: Updated request object.
    """
    try:
        report, primer_set, panel_set = retrieve_sets(
            request=request,
            primer_set_model=primer_set_model,
            panel_model=panel_model,
            analysis=analysis
        )

        # Pre-fetch all sequence objects
        sequence_set = seqid_model.objects.filter(panel__in=panel_set)

        # Create a dictionary for faster lookups
        sequence_dict = {
            (
                seq.primer_id, seq.panel_id, seq.seqid
            ): seq for seq in sequence_set
        }

        # Prepare a list to hold updated sequence objects
        updated_sequences = []

        # Iterate through the primer and panel sets
        for primer in primer_set:
            for panel in panel_set:

                # Get the genus, panel name, and primer name
                genus = panel.genus.lower()
                panel_name = panel.panel
                primer_name = primer.primer_name

                # Get the sequence IDs from the report
                seqids = report.get(primer_name, {}).get(panel_name, {})

                # Iterate through the sequence IDs
                for seqid, sequence_data in seqids.items():
                    # Check if the sequence data is empty
                    if not sequence_data:
                        continue

                    # Get the sequence object from the dictionary
                    sequence = sequence_dict.get((primer.pk, panel.pk, seqid))

                    # Check if the sequence object exists and the names match
                    if (
                        sequence
                        and primer_name == sequence.primer.primer_name
                        and panel_name == sequence.panel.panel
                        and genus == sequence.panel.genus.lower()
                    ):
                        # Update the sequence object with the data from the
                        # report
                        sequence.amplicon_length = sequence_data.get(
                            'amplicon_length', ''
                        )
                        sequence.contig = sequence_data.get('contig', '')
                        sequence.direction = sequence_data.get('direction', '')
                        sequence.forward_mismatch = sequence_data.get(
                            'forward_mismatch', ''
                        )
                        sequence.forward_mismatch_details = sequence_data.get(
                            'forward_mismatch_details', ''
                        )
                        sequence.forward_pos = sequence_data.get(
                            'forward_pos', ''
                        )
                        sequence.forward_query = sequence_data.get(
                            'forward_query', ''
                        )
                        sequence.forward_ref = sequence_data.get(
                            'forward_ref', ''
                        )
                        sequence.primer_set = sequence_data.get(
                            'primer_set', ''
                        )
                        sequence.reverse_mismatch = sequence_data.get(
                            'reverse_mismatch', ''
                        )
                        sequence.reverse_mismatch_details = sequence_data.get(
                            'reverse_mismatch_details', ''
                        )
                        sequence.reverse_pos = sequence_data.get(
                            'reverse_pos', ''
                        )
                        sequence.reverse_query = sequence_data.get(
                            'reverse_query', ''
                        )
                        sequence.reverse_ref = sequence_data.get(
                            'reverse_ref', ''
                        )
                        sequence.sequence = sequence_data.get('sequence', '')
                        start_pos = sequence_data.get('start_pos')
                        sequence.start_pos = start_pos[0] if start_pos else ''
                        stop_pos = sequence_data.get('stop_pos')
                        sequence.stop_pos = stop_pos[0] if stop_pos else ''
                        sequence.total_mismatch = sequence_data.get(
                            'total_mismatch', ''
                        )

                        # Add the sequence object to the list of updated
                        # sequences
                        updated_sequences.append(sequence)

        # Bulk update the sequence objects
        bulk_update(updated_sequences, update_fields=[
            'amplicon_length', 'contig', 'direction', 'forward_mismatch',
            'forward_mismatch_details', 'forward_pos', 'forward_query',
            'forward_ref', 'primer_set', 'reverse_mismatch',
            'reverse_mismatch_details', 'reverse_pos', 'reverse_query',
            'reverse_ref', 'sequence', 'start_pos', 'stop_pos',
            'total_mismatch'
        ])

        return request

    except Exception as exc:
        print(
            'Error in populate_sequence_details: %s',
            str(exc),
        )
        raise


def initialize_data_structures() -> Tuple[Dict, Dict]:
    """
    Initializes the details and totals dictionaries.

    :return: Tuple of dictionaries
    """
    details = {}
    totals = {}
    return details, totals


def initialize_primer_data(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict,
    primer_name: str,
    totals: Dict
) -> Tuple[Dict, Dict]:
    """
    Initializes data structures for a specific primer.

    :param details: Dictionary of details
    :param primer_name: Name of the primer
    :param totals: Dictionary of totals

    :return: Tuple of dictionaries
    """
    if primer_name not in details:
        details[primer_name] = {}
        totals[primer_name] = {}
    return details, totals


def process_sequence_data(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict,
    genus: str,
    panel_name: str,
    primer_name: str,
    sequence: VerifierSEQID,
    totals: Dict
) -> Dict:
    """
    Processes sequence data and updates details and totals.

    :param details: Dictionary of details
    :param genus: Name of the genus
    :param panel_name: Name of the panel
    :param primer_name: Name of the primer
    :param sequence: VerifierSEQID object
    :param totals: Dictionary of totals

    :return: Dictionary of totals
    """
    if not sequence.amplicon_length:
        return totals

    totals = calculate_totals(
        sequence=sequence,
        primer_name=primer_name,
        totals=totals,
        panel_name=panel_name,
        genus=genus
    )

    update_mismatch_details(
        sequence=sequence,
        details=details,
        primer_name=primer_name,
        panel_name=panel_name,
        genus=genus,
        mismatch_type='forward'
    )
    update_mismatch_details(
        sequence=sequence,
        details=details,
        primer_name=primer_name,
        panel_name=panel_name,
        genus=genus,
        mismatch_type='reverse'
    )
    return totals


def update_mismatch_details(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict,
    genus: str,
    mismatch_type: str,
    panel_name: str,
    primer_name: str,
    sequence: VerifierSEQID
) -> Dict:
    """
    Updates mismatch details in the details dictionary.

    :param details: Dictionary of details
    :param genus: Name of the genus
    :param mismatch_type: Type of mismatch
    :param panel_name: Name of the panel
    :param primer_name: Name of the primer
    :param sequence: VerifierSEQID object
    """
    mismatch_details_attr = '{mismatch_type}_mismatch_details'.format(
        mismatch_type=mismatch_type
    )
    mismatch_attr = '{mismatch_type}_mismatch'.format(
        mismatch_type=mismatch_type
    )

    mismatch_details = getattr(sequence, mismatch_details_attr, None)
    mismatch = getattr(sequence, mismatch_attr, None)

    if not mismatch_details:
        return

    for location in [genus, 'combined']:
        if location not in details[primer_name][panel_name]:
            details[primer_name][panel_name][location] = {}

        if (
            '{mismatch_type}_details'.format(mismatch_type=mismatch_type)
            not in details[primer_name][panel_name][location]
        ):
            details[primer_name][panel_name][location][
                '{mismatch_type}_details'.format(
                    mismatch_type=mismatch_type
                )] = {}

        if (
            '{mismatch_type}_mismatches'.format(mismatch_type=mismatch_type)
                not in details[primer_name][panel_name][location]
        ):
            details[primer_name][panel_name][location][
                '{mismatch_type}_mismatches'.format(
                    mismatch_type=mismatch_type
                )
            ] = {}

        # Increment the mismatch details count
        if (
            mismatch_details not in
            details[primer_name][panel_name][location][
                '{mismatch_type}_details'.format(mismatch_type=mismatch_type)]
        ):
            details[primer_name][panel_name][location][
                '{mismatch_type}_details'.format(mismatch_type=mismatch_type)
            ][mismatch_details] = 0
        details[primer_name][panel_name][location][
            '{mismatch_type}_details'.format(mismatch_type=mismatch_type)
        ][mismatch_details] += 1

        # Increment the mismatch count
        if mismatch:
            if (
                mismatch not in
                details[primer_name][panel_name][location][
                    '{mismatch_type}_mismatches'.format(
                        mismatch_type=mismatch_type
                    )]
            ):
                details[primer_name][panel_name][location][
                    '{mismatch_type}_mismatches'.format(
                        mismatch_type=mismatch_type
                    )
                ][mismatch] = 0
            details[primer_name][panel_name][location][
                '{mismatch_type}_mismatches'.format(
                    mismatch_type=mismatch_type
                )
            ][mismatch] += 1
    print('update_mismatch_details: {}'.format(details)) 
    return details


def sort_mismatch_details(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict
) -> None:
    """
    Sorts mismatch details by count in descending order.

    :param details: Dictionary of details
    """
    for _, panel_data in details.items():
        for __, location_data in panel_data.items():
            for location in [
                location_data.get(genus, {})
                for genus in location_data
                if (genus != 'percent' and genus != 'positive'
                    and genus != 'total')
            ] + [location_data.get('combined', {})]:
                for mismatch_type in ['forward', 'reverse']:
                    mismatches_key = '{mismatch_type}_mismatches'.format(
                        mismatch_type=mismatch_type
                    )
                    details_key = '{mismatch_type}_details'.format(
                        mismatch_type=mismatch_type
                    )
                    if isinstance(location, dict):
                        if mismatches_key in location:
                            try:
                                location[mismatches_key] = sorted(
                                    location[mismatches_key].items(),
                                    key=lambda x: x[1],
                                    reverse=True
                                )
                            except AttributeError:
                                pass
                        if details_key in location:
                            try:
                                location[details_key] = sorted(
                                    location[details_key].items(),
                                    key=lambda x: x[1],
                                    reverse=True
                                )
                            except AttributeError:
                                pass


def calculate_panel_percentage(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict,
    genus: str,
    panel_name: str,
    primer_name: str,
    request: PrimerVerifierRequest,
    sequence_set: QuerySet  # Pass the sequence_set
) -> Dict:
    """
    Calculates and updates panel percentage in details.

    :param details: Dictionary of details
    :param genus: Name of the genus
    :param panel_name: Name of the panel
    :param primer_name: Name of the primer
    :param request: PrimerVerifierRequest object
    :param sequence_set: QuerySet of sequences for the panel
    """
    # Calculate genus-specific total
    panel_total = sequence_set.filter(panel__genus=genus).count()

    # Calculate genus-specific positive count
    panel_positive = 0
    for sequence in sequence_set.filter(panel__genus=genus):
        report = json.loads(request.report)
        if (
            primer_name in report
            and panel_name in report[primer_name]
            and sequence.seqid in report[primer_name][panel_name]
            # Check for empty dict
            and report[primer_name][panel_name][sequence.seqid]
        ):
            panel_positive += 1

    try:
        percent = float(
            '{:.2f}'.format(panel_positive / panel_total * 100))
    except ZeroDivisionError:
        percent = 0.00

    details[primer_name][panel_name][genus]['percent'] = percent
    details[primer_name][panel_name][genus]['positive'] = panel_positive
    details[primer_name][panel_name][genus]['total'] = panel_total

    return details


def create_blob_from_summary(
    *,  # Ensure that the following arguments are keyword arguments
    request: PrimerVerifierRequest
) -> None:
    """
    Creates a blob from the summary report in Azure Blob Storage.

    :param request: PrimerVerifierRequest object
    """
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    blob_client.create_blob_from_bytes(
        container_name=request.container_namer(),
        blob_name=os.path.join(
            'reports',
            '{name}_summary_report.json'.format(
                name=request.container_namer())),
        blob=request.summary.encode('utf-8')
    )


def create_details(
    *,  # Ensure that the following arguments are keyword arguments
    panel_model: Type[Union[VerifierPanel, ValidatorPanel]],
    primer_set_model: Type[Union[VerifierPrimerSet, ValidatorPrimerSet]],
    request: Type[Union[PrimerVerifierRequest, ValidatorRequest]],
    analysis: str = 'verifier'
) -> Type[Union[PrimerVerifierRequest, ValidatorRequest]]:
    """
    Parses the report to extract panel- and genus-specific details.

    Args:
        panel_model: Panel model for the current analysis.
        primer_set_model: PrimerSet model for the current analysis.
        request: Request object.
        analysis: Type of analysis. Default is verifier.

    Returns:
        The updated PrimerVerifierRequest object.
    """
    _, primer_set, panel_set = retrieve_sets(
        request=request,
        primer_set_model=primer_set_model,
        panel_model=panel_model,
        analysis=analysis
    )

    details, totals = initialize_data_structures()

    # Load the report data from request.report
    report = json.loads(request.report)

    # Create a dictionary to store seq_dict for each genus
    seq_dict_by_genus = {}

    # Populate seq_dict for each genus
    run_folder = os.path.join(
        'olc_webportalv2',
        'media',
        '{container_name}'.format(
            container_name=request.container_namer())
    )

    seq_dict = {}
    for panel in panel_set:
        genus = panel.genus.lower()

        seq_dict_by_genus = retrieve_panel_seqids(
            genus=genus,
            run_folder=run_folder,
            seq_dictionary=seq_dict,
        )
    # There's apparently some issue with the Listeria/VTEC benchmark dataset,
    # as there is a duplicate seqid in the Listeria dataset. This will be
    # fixed in the future, but for now, we will just report any duplicates
    find_shared_seqids(seq_dictionary=seq_dict_by_genus)

    for primer in primer_set:
        primer_name = primer.primer_name
        details, totals = initialize_primer_data(
            details=details,
            primer_name=primer_name,
            totals=totals
        )

        for panel in panel_set:
            panel_name = panel.panel
            genus = panel.genus.lower()

            if panel_name not in details[primer_name]:
                details[primer_name][panel_name] = {}
                totals[primer_name][panel_name] = {}

            if genus not in details[primer_name][panel_name]:
                details[primer_name][panel_name][genus] = {}
            if 'combined' not in details[primer_name][panel_name]:
                details[primer_name][panel_name]['combined'] = {}

            # Get the sequence IDs from the report for this primer and panel
            seqids = seq_dict_by_genus[genus]

            # Calculate genus-specific total
            panel_total = 0
            panel_positive = 0

            total_dict = {}

            # Probe prevalence
            probe_total = 0
            probe_positive = 0

            # Iterate over a copy of the seqids dictionary
            for seqid in seqids:

                # Extract the sequence data from the report
                sequence_data = report.get(
                    primer_name, {}
                ).get(
                    panel_name, {}
                ).get(
                    seqid, {}
                )

                # Increment the panel total
                panel_total += 1

                # Check if the sequence data is empty
                if not sequence_data:
                    continue

                # Skip if the seqid is 'percent', 'positive', or 'total'
                if seqid in ('percent', 'positive', 'total'):
                    continue

                # Process the sequence data directly from the report
                totals, details, hit = process_sequence_data_from_report(
                    details=details,
                    sequence_data=sequence_data,
                    primer_name=primer_name,
                    panel_name=panel_name,
                    genus=genus,
                    totals=totals
                )

                if hit:
                    # Increment the panel positive count
                    panel_positive += 1
                    total_dict.update(totals)

                # Probe prevalence
                probe_data = sequence_data.get('probe')
                if probe_data and isinstance(probe_data, dict):
                    probe_total += 1
                    # Look for probe hits
                    probe_hit = False
                    if 'probe' in probe_data and isinstance(
                        probe_data['probe'], dict
                    ):
                        probe_info = probe_data['probe']
                        if float(probe_info.get('percent_id', 0)) >= 90.0:
                            probe_hit = True
                    elif 'percent_id' in probe_data:
                        if float(probe_data.get('percent_id', 0)) >= 90.0:
                            probe_hit = True
                    if probe_hit:
                        probe_positive += 1

            # Calculate panel percentages *after* the loop
            panel_percentages = calculate_panel_percentage_from_counts(
                panel_total=panel_total,
                panel_positive=panel_positive
            )

            # Update the details dictionary with the calculated percentages
            details[primer_name][panel_name][genus]['percent'] = (
                panel_percentages['percent'])
            details[primer_name][panel_name][genus]['positive'] = (
                panel_percentages['positive'])
            details[primer_name][panel_name][genus]['total'] = (
                panel_percentages['total'])

            # Store probe prevalence stats if any probe data was found 
            if probe_total > 0:
                try:
                    probe_percent = float(
                        "{:.2f}".format(probe_positive / panel_total * 100)
                    )
                except ZeroDivisionError:
                    probe_percent = 0.00
                details[primer_name][panel_name][genus]["probe_prevalence"] = {
                    "percent": probe_percent,
                    "positive": probe_positive,
                    "total": panel_total,
                }

    sort_mismatch_details(
        details=details
    )

    request.summary = json.dumps(
        details, sort_keys=True, indent=4, separators=(',', ': '))
    request.totals = json.dumps(
        totals, sort_keys=True, indent=4, separators=(',', ': '))
    request.save()

    # Create a blob from the summary report
    create_blob_from_summary(
        request=request
    )

    return request


def find_shared_seqids(
    *,  # Ensure that the following arguments are keyword arguments
    seq_dictionary: Dict
) -> Dict:
    """
    Finds seqids that are present in more than one genus-specific list.

    Args:
        seq_dictionary: A dictionary where keys are genus names (str) and
                        values are lists of seqids (str).

    Returns:
        A dictionary where keys are genus names (str) and values are sets of
        seqids (str) that are shared with other genera.
    """

    shared_seqids = {}
    seqid_to_genera = {}

    # Map each seqid to the genera it belongs to
    for genus, seqids in seq_dictionary.items():
        for seqid in seqids:
            if seqid not in seqid_to_genera:
                seqid_to_genera[seqid] = []
            seqid_to_genera[seqid].append(genus)

    # Identify shared seqids and the genera that share them
    for seqid, genera in seqid_to_genera.items():
        if len(genera) > 1:
            # Sort for consistent ordering
            genera_str = ','.join(sorted(genera))
            shared_seqids[genera_str] = seqid

    print('shared_seqids: {}'.format(shared_seqids))
    return shared_seqids


def process_sequence_data_from_report(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict,
    sequence_data: Dict,
    primer_name: str,
    panel_name: str,
    genus: str,
    totals: Dict
) -> Dict:
    """
    Processes sequence data from the report and updates details and totals.

    :param details: Dictionary of details
    :param sequence_data: Sequence data from the report
    :param primer_name: Name of the primer
    :param panel_name: Name of the panel
    :param genus: Name of the genus
    :param totals: Dictionary of totals

    :return: Dictionary of totals
    """
    try:
        amplicon_length = sequence_data.get('amplicon_length')
    except AttributeError:
        print("Error: amplicon_length not found in sequence_data")
        # print(sequence_data)
        raise
    if not amplicon_length:
        return totals, details, False

    totals = calculate_totals_from_report(
        sequence_data=sequence_data,
        primer_name=primer_name,
        totals=totals,
        panel_name=panel_name,
        genus=genus
    )
    
    details = update_mismatch_details_from_report(
        sequence_data=sequence_data,
        details=details,
        primer_name=primer_name,
        panel_name=panel_name,
        genus=genus
    )
    # details = update_mismatch_details_from_report(
    #     sequence_data=sequence_data,
    #     details=details,
    #     primer_name=primer_name,
    #     panel_name=panel_name,
    #     genus=genus,
    #     mismatch_type='reverse'
    # )
    
    print('process_sequence_data_from_report totals: {}'.format(totals))
    # print('process_sequence_data_from_report details: {}'.format(details))
    
    return totals, details, True


def calculate_totals_from_report(
    *,  # Enforce keyword arguments
    sequence_data: Dict[str, Any],
    totals: Dict[str, Any],
    primer_name: str,
    panel_name: str,
    genus: str
) -> Dict[str, Any]:
    """
    Calculates totals from sequence data in the report.

    Args:
        sequence_data: Sequence data from the report.
        totals: Dictionary to store the calculated totals.
        primer_name: Name of the primer.
        panel_name: Name of the panel.
        genus: Name of the genus.

    Returns:
        The updated totals dictionary.
    """
    # Get the panel totals dictionary
    panel_totals = totals[primer_name][panel_name]

    # Initialize genus and combined totals dictionaries
    genus_totals = panel_totals.setdefault(genus, {})
    combined_totals = panel_totals.setdefault('combined', {})

    # Get the total mismatch count
    total_mismatch = int(sequence_data.get('total_mismatch', 0))

    # Initialize total mismatch dictionaries
    genus_mismatch_totals = genus_totals.setdefault(
        'total_mismatch', {})
    combined_mismatch_totals = combined_totals.setdefault(
        'total_mismatch', {})

    # Initialize mismatch counts dictionaries
    genus_mismatch_counts = genus_mismatch_totals.setdefault(
        total_mismatch, {})
    combined_mismatch_counts = combined_mismatch_totals.setdefault(
        total_mismatch, {})

    # Increment the total counts
    genus_mismatch_counts['totals'] = (
        genus_mismatch_counts.get('totals', 0) + 1)
    combined_mismatch_counts['totals'] = (
        combined_mismatch_counts.get('totals', 0) + 1)

    # Get forward and reverse mismatch details
    forward_mismatch_details = sequence_data.get(
        'forward_mismatch_details', '0')
    reverse_mismatch_details = sequence_data.get(
        'reverse_mismatch_details', '0')

    # Combine mismatch details
    total_details = '{forward}, {reverse}'.format(
        forward=(
            forward_mismatch_details if forward_mismatch_details else '0'
        ),
        reverse=(
            reverse_mismatch_details if reverse_mismatch_details else '0'
        )
    )

    # Increment the mismatch details counts
    genus_mismatch_counts[total_details] = (
        genus_mismatch_counts.get(total_details, 0) + 1)
    combined_mismatch_counts[total_details] = (
        combined_mismatch_counts.get(total_details, 0) + 1)

    return totals


def update_mismatch_details_from_report(
    *,  # Enforce keyword arguments
    details: Dict[str, Any],
    genus: str,
    panel_name: str,
    primer_name: str,
    sequence_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Updates mismatch details in the details dictionary.

    Updates mismatch details directly from the report data.

    Args:
        details: Dictionary to store the mismatch details.
        genus: Name of the genus.
        panel_name: Name of the panel.
        primer_name: Name of the primer.
        sequence_data: Sequence data from the report.

    Returns:
        Dict[str, Any]: The updated details dictionary.
    """
    for mismatch_type in ['forward', 'reverse']:
        # Get mismatch details and count from sequence data
        mismatch_details = sequence_data.get(
            '{mismatch_type}_mismatch_details'.format(
                mismatch_type=mismatch_type))
        mismatch = sequence_data.get(
            '{mismatch_type}_mismatch'.format(mismatch_type=mismatch_type))

        # If no mismatch details, continue
        if not mismatch_details:
            continue

        for location in [genus, 'combined']:
            # Get panel data, initializing if necessary
            if location not in details[primer_name][panel_name]:
                details[primer_name][panel_name][location] = {}

            # Initialize mismatch details dictionary if necessary
            mismatch_details_key = '{mismatch_type}_details'.format(
                mismatch_type=mismatch_type)
            if mismatch_details_key not in details[primer_name][panel_name][location]:
                details[primer_name][panel_name][location][mismatch_details_key] = {}

            # Initialize mismatch counts dictionary if necessary
            mismatch_counts_key = '{mismatch_type}_mismatches'.format(
                mismatch_type=mismatch_type)
            if mismatch_counts_key not in details[primer_name][panel_name][location]:
                details[primer_name][panel_name][location][mismatch_counts_key] = {}

            # Increment the mismatch details count
            if mismatch_details not in details[primer_name][panel_name][location][mismatch_details_key]:
                details[primer_name][panel_name][location][mismatch_details_key][mismatch_details] = 0
            details[primer_name][panel_name][location][mismatch_details_key][mismatch_details] += 1

            # Increment the mismatch count
            if mismatch:
                if mismatch not in details[primer_name][panel_name][location][mismatch_counts_key]:
                    details[primer_name][panel_name][location][mismatch_counts_key][mismatch] = 0
                details[primer_name][panel_name][location][mismatch_counts_key][mismatch] += 1
    return details


def calculate_panel_percentage_from_counts(
    *,  # Ensure that the following arguments are keyword arguments
    panel_total: int,
    panel_positive: int
) -> Dict:
    """
    Calculates and returns panel percentage.

    Args:
        panel_total (int): Total count for the panel.
        panel_positive (int): Positive count for the panel.

    Returns:
        Dict: Panel percentage details.
    """
    try:
        percent = float(
            '{:.2f}'.format(panel_positive / panel_total * 100))
    except ZeroDivisionError:
        percent = 0.00

    print("panel_total: {}".format(panel_total))
    print("panel_positive: {}".format(panel_positive))

    return {
        'percent': percent,
        'positive': panel_positive,
        'total': panel_total
    }


def calculate_totals(sequence, totals, primer_name, panel_name, genus):
    if genus not in totals[primer_name][panel_name]:
        totals[primer_name][panel_name][genus] = dict()
    if 'combined' not in totals[primer_name][panel_name]:
        totals[primer_name][panel_name]['combined'] = dict()
    if 'total_mismatch' not in totals[primer_name][panel_name][genus]:
        totals[primer_name][panel_name][genus]['total_mismatch'] = dict()
        totals[primer_name][panel_name]['combined']['total_mismatch'] = dict()
    total_mismatch = int(sequence.total_mismatch)
    if total_mismatch not in totals[primer_name][panel_name][genus]['total_mismatch']:
        totals[primer_name][panel_name][genus]['total_mismatch'][total_mismatch] = dict()
        totals[primer_name][panel_name]['combined']['total_mismatch'][total_mismatch] = dict()
    if 'totals' not in totals[primer_name][panel_name][genus]['total_mismatch'][total_mismatch]:
        totals[primer_name][panel_name][genus]['total_mismatch'][total_mismatch]['totals'] = 0
        totals[primer_name][panel_name]['combined']['total_mismatch'][total_mismatch]['totals'] = 0
    totals[primer_name][panel_name][genus]['total_mismatch'][total_mismatch]['totals'] += 1
    totals[primer_name][panel_name]['combined']['total_mismatch'][total_mismatch]['totals'] += 1
    total_details = '{forward}, {reverse}'.format(
        forward=sequence.forward_mismatch_details if sequence.forward_mismatch_details else '0',
        reverse=sequence.reverse_mismatch_details if sequence.reverse_mismatch_details else '0'
    )
    if total_details not in totals[primer_name][panel_name][genus]['total_mismatch'][total_mismatch]:
        totals[primer_name][panel_name][genus]['total_mismatch'][total_mismatch][total_details] = 0
    if total_details not in totals[primer_name][panel_name]['combined']['total_mismatch'][total_mismatch]:
        totals[primer_name][panel_name]['combined']['total_mismatch'][total_mismatch][total_details] = 0
    totals[primer_name][panel_name][genus]['total_mismatch'][total_mismatch][total_details] += 1
    totals[primer_name][panel_name]['combined']['total_mismatch'][total_mismatch][total_details] += 1
    # print(totals)
    # if sequence.forward_mismatch_details and sequence.reverse_mismatch_details:
    # print(sequence.seqid, sequence.forward_mismatch_details, sequence.reverse_mismatch_details,
    #           sequence.total_mismatch)
    return totals


def exclusivity_panel_retrieve(inclusivity):
    """
    Extract corresponding exclusivity panel for a given inclusivity panel
    :param inclusivity: type query_set:
    ValidatorRequest.objects.filter(pk=primary_key)[0].panel_details
    The query returns a list containing the genus in the appropriate panel
    (e.g. ['escherichia'])
    """
    # Dictionary to store the inclusivity: exclusivity validation panels
    # Iterate through all the genera within the desired panel
    inclusivity_exclusivity_dict = {
        'Campylobacter': ['Escherichia', 'Listeria', 'Salmonella'],
        'Escherichia': ['Campylobacter', 'Listeria', 'Salmonella'],
        'Listeria': ['Campylobacter', 'Escherichia', 'Salmonella'],
        'Salmonella': ['Campylobacter', 'Escherichia', 'Listeria'],
        'Vtec': ['Escherichia']
    }
    # The genera in the model are lowercase, so they must be manipulated to
    # the desired formatting e.g. Escherichia
    inclusivity = inclusivity.capitalize()
    # Extract the exclusivity panel from the dictionary
    exclusivity_panel = inclusivity_exclusivity_dict[inclusivity]
    return exclusivity_panel


def _upload_summary_excel(blob_client, container_name, request):
    """
    Create an Excel workbook from request_obj.summary JSON and upload it as:
    reports/<container_namer>_summary_report.xlsx
    """
    if xlsxwriter is None:
        # xlsxwriter not installed; skip
        return

    summary_text = getattr(request, "summary", None)
    report_text = getattr(request, "report", None)
    # Require at least one data source (summary or report)
    if not summary_text and not report_text:
        return

    try:
        summary = json.loads(summary_text) if summary_text else {}
    except Exception:
        return

    try:
        report = json.loads(report_text) if report_text else {}
    except Exception:
        return

    # Build a mapping (PANEL, SEQID) -> primer_sets and per-primer seq data
    entries = {}  # type: Dict[Tuple[str, str], Dict[str, Any]]
    if isinstance(report, dict):
        for primer_set_name, primer_data in report.items():
            if primer_set_name == 'validator_version':
                continue
            if not isinstance(primer_data, dict):
                continue
            for panel_name, panel_dict in primer_data.items():
                if not isinstance(panel_dict, dict):
                    continue
                for seqid, seq_data in panel_dict.items():
                    if seqid in ('percent', 'positive', 'total'):
                        continue
                    key = (panel_name.upper(), seqid)
                    ent = entries.setdefault(key, {"primer_sets": [], "per_primer": {}})
                    if primer_set_name not in ent["primer_sets"]:
                        ent["primer_sets"].append(primer_set_name)
                    # Store seq_data per primer (None for empty entries)
                    ent["per_primer"][primer_set_name] = seq_data if seq_data else None

    def _collect_stats_rows(node, path):
        rows = []
        if isinstance(node, dict):
            if (("total" in node) or ("positive" in node) or ("percent" in node)):
                primer = path[0] if len(path) > 0 else ""
                panel = path[1] if len(path) > 1 else ""
                genus = path[2] if len(path) > 2 else ""
                rows.append((primer, panel, genus, node.get("total"), node.get("positive"), node.get("percent")))
            for k, v in node.items():
                rows.extend(_collect_stats_rows(v, path + [str(k)]))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                rows.extend(_collect_stats_rows(v, path + [str(i)]))
        return rows

    def _emit_flat(ws, node, prefix, row_idx):
        """
        Write flattened (path, value) rows to worksheet ws starting at row_idx.
        Returns next free row index.
        """
        if isinstance(node, dict):
            # Sort keys for determinism
            for k in sorted(node.keys()):
                v = node[k]
                next_prefix = prefix + ("." if prefix else "") + str(k)
                row_idx = _emit_flat(ws, v, next_prefix, row_idx)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                next_prefix = prefix + ("/" if prefix else "") + str(i)
                row_idx = _emit_flat(ws, v, next_prefix, row_idx)
        else:
            # Leaf
            try:
                val = json.dumps(node)
            except Exception:
                val = str(node)
            ws.write(row_idx, 0, prefix)
            ws.write(row_idx, 1, val)
            row_idx += 1
        return row_idx

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        workbook = xlsxwriter.Workbook(tmp_path)
        header_fmt = workbook.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1})
        cell_fmt = workbook.add_format({"border": 1})
        pct_fmt = workbook.add_format({"num_format": "0.00%", "border": 1})
        int_fmt = workbook.add_format({"num_format": "0", "border": 1})

        # PrimerVerifierResults (first sheet)
        ws0 = workbook.add_worksheet("PrimerVerifierResults")
        headers0 = [
            "SEQID",
            "PANEL",
            "primer set",
            "amplicon length",
            "contig",
            "location",
            "direction",
            "forward mismatch",
            "forward mismatch details",
            "reverse mismatch",
            "reverse mismatch details",
            "probe mismatches",
            "probe mismatch details",
            "Total Mismatches",
        ]
        for col, h in enumerate(headers0):
            ws0.write(0, col, h, header_fmt)

        # Determine ordering (EXCLUSIVITY first, then INCLUSIVITY), then by seqid
        panel_order = {"EXCLUSIVITY": 1, "INCLUSIVITY": 0}
        sorted_keys = sorted(entries.keys(), key=lambda k: (panel_order.get(k[0], 2), k[1]))

        write_row = 1

        def _get_field(sd, keys):
            if not sd or not isinstance(sd, dict):
                return None
            for k in keys:
                if k in sd:
                    return sd.get(k)
                lk = k.lower()
                if lk in sd:
                    return sd.get(lk)
            return None

        for panel, seqid in sorted_keys:
            ent = entries[(panel, seqid)]
            primer_list = sorted(ent["primer_sets"])
            # Primer sets that have seq_data (non-empty)
            present = [p for p in primer_list if ent["per_primer"].get(p)]

            def _write_row_from_sd(sd, primer_cell):
                # sd may be a dict or None
                if not sd:
                    amplicon_length = "ND"
                    contig = "ND"
                    location = "ND"
                    direction = "ND"
                    forward_mismatch = "ND"
                    forward_mismatch_details = ""
                    reverse_mismatch = "ND"
                    reverse_mismatch_details = ""
                    probe_mismatches = "ND"
                    probe_mismatch_details = ""
                    total_mismatch = "ND"
                else:
                    amplicon_length = _get_field(
                        sd,
                        [
                            "amplicon_size",
                            "AmpliconSize",
                            "amplicon_length"
                        ]
                    ) or "ND"
                    contig = _get_field(
                        sd,
                        [
                            "contig",
                            "Contig"
                        ]
                    ) or "ND"
                    location = _get_field(
                        sd,
                        [
                            "location",
                            "amplicon_range",
                            "GenomeLocation"
                        ]
                    ) or "ND"
                    direction = _get_field(
                        sd,
                        [
                            "orientation",
                            "direction"
                        ]
                    ) or "ND"
                    forward_mismatch = _get_field(
                        sd,
                        [
                            "forward_mismatch",
                            "ForwardMismatches"
                        ]
                    ) or "ND"
                    forward_mismatch_details = _get_field(
                        sd,
                        [
                            "forward_mismatch_details",
                            "ForwardMismatchDetails"
                        ]
                    ) or ""
                    reverse_mismatch = _get_field(
                        sd,
                        [
                            "reverse_mismatch",
                            "ReverseMismatches"
                        ]
                    ) or "ND"
                    reverse_mismatch_details = _get_field(
                        sd,
                        [
                            "reverse_mismatch_details",
                            "ReverseMismatchDetails"
                        ]
                    ) or ""

                    # Probe extraction (handle nested/flat forms)
                    probe_mismatches = "ND"
                    probe_mismatch_details = ""
                    probe_data = sd.get(
                        "probe"
                    ) if isinstance(sd, dict) else None
                    if probe_data and isinstance(probe_data, dict):
                        try:
                            if "probe" in probe_data and isinstance(
                                probe_data["probe"], dict
                            ):
                                pinfo = probe_data["probe"]
                            else:
                                kprobe = next(iter(probe_data.keys()))
                                pinfo = probe_data[kprobe] if isinstance(
                                    probe_data[kprobe], dict
                                ) else probe_data
                            probe_mismatches = pinfo.get(
                                "mismatches",
                                pinfo.get("mismatch", "ND")
                            )
                            probe_mismatch_details = pinfo.get(
                                "mismatch_details", "")
                        except Exception:
                            probe_mismatches = probe_data.get(
                                "mismatches", "ND"
                            )

                    # Compute total mismatches
                    def _parse_mismatch_value(val):
                        if val is None:
                            return None
                        sval = str(val).strip()
                        if sval == "" or sval.upper() == "ND":
                            return None
                        try:
                            return int(sval)
                        except Exception:
                            try:
                                return int(float(sval))
                            except Exception:
                                return None

                    fval = _parse_mismatch_value(forward_mismatch)
                    rval = _parse_mismatch_value(reverse_mismatch)
                    pval = _parse_mismatch_value(probe_mismatches)
                    if fval is None and rval is None and pval is None:
                        total_mismatch = "ND"
                    else:
                        total_mismatch = (fval or 0) + (rval or 0) + (pval or 0)

                ws0.write(write_row, 0, seqid, cell_fmt)
                ws0.write(write_row, 1, panel, cell_fmt)
                ws0.write(write_row, 2, primer_cell, cell_fmt)
                ws0.write(write_row, 3, amplicon_length, cell_fmt)
                ws0.write(write_row, 4, contig, cell_fmt)
                ws0.write(write_row, 5, location, cell_fmt)
                ws0.write(write_row, 6, direction, cell_fmt)
                ws0.write(write_row, 7, forward_mismatch, cell_fmt)
                ws0.write(write_row, 8, forward_mismatch_details, cell_fmt)
                ws0.write(write_row, 9, reverse_mismatch, cell_fmt)
                ws0.write(write_row, 10, reverse_mismatch_details, cell_fmt)
                ws0.write(write_row, 11, probe_mismatches, cell_fmt)
                ws0.write(write_row, 12, probe_mismatch_details, cell_fmt)
                ws0.write(write_row, 13, total_mismatch, cell_fmt)

            if present:
                for primer_name in present:
                    sd = ent["per_primer"][primer_name]
                    _write_row_from_sd(sd, primer_name)
                    write_row += 1
            else:
                primer_sets = ", ".join(primer_list)
                sd = None
                _write_row_from_sd(sd, primer_sets)
                write_row += 1

        ws0.autofilter(0, 0, max(write_row - 1, 0), len(headers0) - 1)
        ws0.freeze_panes(1, 0)
        ws0.set_column(0, 0, 20)
        ws0.set_column(1, 1, 12)
        ws0.set_column(2, 2, 30)
        ws0.set_column(3, 6, 14)
        ws0.set_column(7, 12, 22)
        ws0.set_column(13, 13, 16)

        # Overview
        ws = workbook.add_worksheet("Overview")
        headers = ["Primer", "Panel", "Genus", "Total", "Positive", "Percent"]
        for col, h in enumerate(headers):
            ws.write(0, col, h, header_fmt)

        stats_rows = _collect_stats_rows(summary, [])
        # dedupe while preserving order
        seen = set()
        write_row = 1
        for r in stats_rows:
            if r in seen:
                continue
            seen.add(r)
            # r = (primer, panel, genus, total, positive, percent)
            ws.write(write_row, 0, r[0], cell_fmt)
            ws.write(write_row, 1, r[1], cell_fmt)
            ws.write(write_row, 2, r[2], cell_fmt)
            # numeric cells
            if r[3] is None:
                ws.write_blank(write_row, 3, None, cell_fmt)
            else:
                ws.write_number(write_row, 3, r[3], int_fmt)
            if r[4] is None:
                ws.write_blank(write_row, 4, None, cell_fmt)
            else:
                ws.write_number(write_row, 4, r[4], int_fmt)
            # percent can be 0-100 or 0-1 depending on your JSON; try to normalize
            percent_val = r[5]
            if isinstance(percent_val, (int, float)):
                if percent_val > 1.0:
                    percent_val = percent_val / 100.0
                ws.write_number(write_row, 5, percent_val, pct_fmt)
            elif percent_val is None:
                ws.write_blank(write_row, 5, None, cell_fmt)
            else:
                # fallback as text
                ws.write(write_row, 5, str(percent_val), cell_fmt)
            write_row += 1

        ws.autofilter(0, 0, max(write_row - 1, 0), len(headers) - 1)
        ws.freeze_panes(1, 0)
        ws.set_column(0, 0, 22)
        ws.set_column(1, 1, 22)
        ws.set_column(2, 2, 18)
        ws.set_column(3, 4, 10)
        ws.set_column(5, 5, 10)

        # RawSummaryJSON
        ws2 = workbook.add_worksheet("RawSummaryJSON")
        ws2.write(0, 0, "Path", header_fmt)
        ws2.write(0, 1, "Value", header_fmt)
        end_row = _emit_flat(ws2, summary, "", 1)
        ws2.autofilter(0, 0, max(end_row - 1, 0), 1)
        ws2.freeze_panes(1, 0)
        ws2.set_column(0, 0, 70)
        ws2.set_column(1, 1, 80)

        # MismatchTotals (if present)
        totals_text = getattr(request, "totals", None)
        if totals_text:
            try:
                totals_json = json.loads(totals_text)
                ws3 = workbook.add_worksheet("MismatchTotals")
                ws3.write(0, 0, "Path", header_fmt)
                ws3.write(0, 1, "Value", header_fmt)
                end_row3 = _emit_flat(ws3, totals_json, "", 1)
                ws3.autofilter(0, 0, max(end_row3 - 1, 0), 1)
                ws3.freeze_panes(1, 0)
                ws3.set_column(0, 0, 70)
                ws3.set_column(1, 1, 80)
            except Exception:
                pass

        workbook.close()

        # Upload next to JSON summary
        blob_name = "reports/{0}_summary_report.xlsx".format(container_name)
        blob_client.create_blob_from_path(
            container_name=container_name,
            blob_name=blob_name,
            file_path=tmp_path,
            content_settings=ContentSettings(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _archive_reports_and_upload(
    *,  # Force keyword args
    blob_client: BlockBlobService,
    container_name: str
):
    """
    Download all blobs under reports/, zip them, and upload the archive to the
    container root as <container_name>_reports.zip. Returns the blob name.
    """

    # Collect the list of report blobs (prefix 'reports/')
    report_blobs = [
        b
        for b in blob_client.list_blobs(
            container_name=container_name, prefix="reports/"
        )
    ]

    if not report_blobs:
        # Nothing to archive
        return "{0}_reports.zip".format(container_name)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Local base "reports" dir to mirror the virtual folder
        local_reports_dir = os.path.join(tmpdir, "reports")
        os.makedirs(local_reports_dir)

        # Download each blob to the temp dir
        for b in report_blobs:
            local_path = os.path.join(tmpdir, b.name)  # includes 'reports/...' in path
            local_dir = os.path.dirname(local_path)
            if not os.path.isdir(local_dir):
                os.makedirs(local_dir)
            blob_client.get_blob_to_path(
                container_name=container_name, blob_name=b.name, file_path=local_path
            )

        # Create zip archive that includes the "reports" folder at top-level
        base_name = os.path.join(tmpdir, "{0}_reports".format(container_name))
        archive_path = shutil.make_archive(
            base_name=base_name, format="zip",
            root_dir=tmpdir,
            base_dir="reports"
        )

        # Upload to the container root
        zip_blob_name = "{0}_reports.zip".format(container_name)
        blob_client.create_blob_from_path(
            container_name=container_name,
            blob_name=zip_blob_name,
            file_path=archive_path,
            content_settings=ContentSettings(content_type="application/zip"),
        )
        return zip_blob_name
