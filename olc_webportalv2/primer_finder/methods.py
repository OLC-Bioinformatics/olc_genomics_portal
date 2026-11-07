#!/usr/bin/env python

"""
Methods for the primer_finder app
"""

# Standard imports
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import csv
import glob
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
from olc_webportalv2.common.benchmarks import validate_benchmark_panel
from olc_webportalv2.cowbat.methods import AzureBatch
from olc_webportalv2.primer_finder.models import (
    VerifierRequest,
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

    # Consolidated Validator requests store all submitted target primers in
    # primer_sequences; retain the legacy single-pair fallback.
    primer_sequences = getattr(request, 'primer_sequences', '')
    if not primer_sequences:
        primer_sequences = '{forward_primer}\r\n{reverse_primer}\r\n'\
            .format(forward_primer=request.forward_primer,
                    reverse_primer=request.reverse_primer)

    # Write the bytes-encoded primer sequences to blob storage
    blob_client.create_blob_from_bytes(
        container_name=container_name,
        blob_name=file_name,
        blob=primer_sequences.encode('utf-8'))

    return container_name, file_name


def _validator_probe_records(request):
    """Return configured target probes, or the legacy request-level probe."""
    if isinstance(request, ValidatorRequest):
        target_probes = []
        for sample in request.samples.prefetch_related('targets'):
            for target in sample.targets.all():
                sequence = (target.probe_sequence or '').strip()
                if sequence:
                    target_probes.append((target.name, sequence))
        if target_probes:
            return target_probes

    sequence = (getattr(request, 'probe_sequence', '') or '').strip()
    return [('probe', sequence)] if sequence else []


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

    probe_sequence = '\r\n'.join(
        '>{0}\r\n{1}'.format(name, sequence)
        for name, sequence in _validator_probe_records(request)
    ) + '\r\n'

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
        server = None
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
        pk (int): Primary key of the VerifierRequest.
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
            verifier_request_id=verifier_request_pk). The query
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
    *,
    genera: list,
    panel_type: str,
    pk: int,
    panel: Type[Union[VerifierPanel, ValidatorPanel]] = VerifierPanel
):
    """
    Populate the database using the supplied panel.

    Ordinary benchmarks may be used in either panel, but the same benchmark
    may not be added to both panels. Benchmarks whose canonical identifiers
    end with ``-exclusivity`` may only be used in the exclusivity panel.
    """
    if panel_type not in ("inclusivity", "exclusivity"):
        raise ValueError("Unsupported panel type: {0}".format(panel_type))

    id_string = (
        "verifier_request_id" if panel == VerifierPanel else "validator_request_id"
    )
    opposite_panel_type = (
        "exclusivity" if panel_type == "inclusivity" else "inclusivity"
    )

    existing_panels = panel.objects.filter(
        **{
            id_string: pk,
            "panel": panel_type,
        }
    ).values_list("genus", flat=True)

    opposite_panels = panel.objects.filter(
        **{
            id_string: pk,
            "panel": opposite_panel_type,
        }
    ).values_list("genus", flat=True)

    # Existing requests may store title-case benchmark names.
    existing_panels_set = set(
        str(existing_panel).strip().lower() for existing_panel in existing_panels
    )
    opposite_panels_set = set(
        str(opposite_panel).strip().lower() for opposite_panel in opposite_panels
    )

    new_panel_objects = []

    for genus in genera:
        canonical_name = validate_benchmark_panel(
            benchmark_name=genus, panel=panel_type
        )

        if canonical_name in opposite_panels_set:
            raise ValueError(
                "Benchmark dataset {0} cannot be assigned to both "
                "inclusivity and exclusivity.".format(canonical_name)
            )

        if canonical_name not in existing_panels_set:
            new_panel_objects.append(
                panel(
                    **{
                        id_string: pk,
                        "genus": canonical_name,
                        "panel": panel_type,
                    }
                )
            )
            existing_panels_set.add(canonical_name)

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
    # Prefer the legacy filename, then support SEQID files named from a
    # versioned benchmark archive such as ``bds-salmonella-1.0.0_seqids.txt``.
    legacy_seqids_file = os.path.join(
        run_folder, "{genus}_seqids.txt".format(genus=genus)
    )
    seqids_file = legacy_seqids_file
    if not os.path.exists(seqids_file):
        versioned_files = glob.glob(
            os.path.join(run_folder, "{0}-*_seqids.txt".format(genus))
        )

        def version_key(path):
            filename = os.path.basename(path)
            version = filename[len(genus) + 1:-len('_seqids.txt')]
            try:
                return tuple(int(part) for part in version.split('.'))
            except ValueError:
                return ()

        if versioned_files:
            seqids_file = max(versioned_files, key=version_key)

    # Check if the file exists
    if not os.path.exists(seqids_file):
        print("Warning: {seqids} not found.".format(seqids=seqids_file))
        return seq_dictionary

    # Read the SEQIDs from the file
    with open(seqids_file, "r", encoding='utf-8') as f:
        seqids = [line.strip() for line in f]

    # Initialise the genus key as required.
    if genus not in seq_dictionary:
        seq_dictionary[genus] = []

    existing_seqids = set(seq_dictionary[genus])

    for seqid in seqids:
        if "ge,omic" in seqid:
            seqid = seqid.replace("ge,omic", "genomic")

        if seqid not in existing_seqids:
            seq_dictionary[genus].append(seqid)
            existing_seqids.add(seqid)

    return seq_dictionary


def populate_report(
    *,  # Ensure that the following arguments are keyword arguments
    request: Type[Union[VerifierRequest, ValidatorRequest]]
) -> Tuple[Union[VerifierRequest, ValidatorRequest], str]:
    """
    Download the JSON-formatted report from blob storage, parse it, and store
    the contents in the VerifierRequest model.

    Args:
        request (VerifierRequest): VerifierRequest object.

    Returns:
        Tuple[VerifierRequest, str]: Updated request and path to the
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
    request: VerifierRequest
) -> VerifierRequest:
    """
    Load the JSON report and store it in the VerifierRequest model.

    Args:
        report_folder (str): The path to the reports folder.
        request (VerifierRequest): The request object.

    Returns:
        VerifierRequest: The updated request object.
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
    request: Union[VerifierRequest, ValidatorRequest],
    primer_set_model: Union[VerifierPrimerSet, ValidatorPrimerSet],
    panel_model: Union[VerifierPanel, ValidatorPanel],
    analysis: str
):
    """
    Extract the primer and panel query sets from models using the primary key
    of a request

    :param request: type django.db.models.Model: VerifierRequest or
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
    request: Union[VerifierRequest, ValidatorRequest],
    primer_set_model: Union[VerifierPrimerSet, ValidatorPrimerSet],
    panel_model: Union[VerifierPanel, ValidatorPanel],
    seqid_model: Union[VerifierSEQID, ValidatorSEQID],
    analysis: str = 'verifier'
) -> Union[VerifierRequest, ValidatorRequest]:
    """
    Parse the JSON-formatted report and populate the SEQID model with
    sequence-specific information.

    Args:
        request (Union[VerifierRequest, ValidatorRequest]): Request
            object.
        primer_set_model (Union[VerifierPrimerSet, ValidatorPrimerSet]):
            PrimerSet model.
        panel_model (Union[VerifierPanel, ValidatorPanel]): Panel model.
        seqid_model (Union[VerifierSEQID, ValidatorSEQID]): SEQID model.
        analysis (str): Analysis type ('verifier' or 'validator').

    Returns:
        Union[VerifierRequest, ValidatorRequest]: Updated request object.
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
                                    key=lambda x: _mismatch_sort_key(x[1]),
                                    reverse=True
                                )
                            except AttributeError:
                                pass
                        if details_key in location:
                            try:
                                location[details_key] = sorted(
                                    location[details_key].items(),
                                    key=lambda x: _mismatch_sort_key(x[1]),
                                    reverse=True
                                )
                            except AttributeError:
                                pass


def _mismatch_sort_key(value):
    """Return a Python 3.5-safe ordering key for mixed mismatch counts."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = -1
    return (
        value is not None,
        numeric_value,
        str(value or ''),
    )


def calculate_panel_percentage(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict,
    genus: str,
    panel_name: str,
    primer_name: str,
    request: VerifierRequest,
    sequence_set: QuerySet  # Pass the sequence_set
) -> Dict:
    """
    Calculates and updates panel percentage in details.

    :param details: Dictionary of details
    :param genus: Name of the genus
    :param panel_name: Name of the panel
    :param primer_name: Name of the primer
    :param request: VerifierRequest object
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
    request: VerifierRequest
) -> None:
    """
    Creates a blob from the summary report in Azure Blob Storage.

    :param request: VerifierRequest object
    """
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    blob_client.create_blob_from_bytes(
        container_name=request.container_namer(),
        blob_name=os.path.join(
            'reports',
            '{0}_summary_report.json'.format(request.container_namer())
        ),
        blob=request.summary.encode('utf-8')
    )


GENE_CATEGORIES = (
    'FRAME_SHIFT', 'INTERNAL_STOP', 'PARTIAL_CONTIG_END', 'EXTENDED'
)
GENE_MIN_PERCENT_IDENTITY = 90.0


def _gene_value(record, *names):
    """Read a GeneSeekr/BLAST field while accepting common aliases."""
    raw = record.get('raw', record) if isinstance(record, dict) else {}
    for name in names:
        value = record.get(name) if isinstance(record, dict) else None
        if value in (None, '') and isinstance(raw, dict):
            value = raw.get(name)
        if value not in (None, ''):
            return value
    return None


def _gene_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gene_reference_has_terminal_stop(record):
    """Return whether a complete reference ends with TAA, TAG, or TGA."""
    sequence = _gene_sequence(
        record,
        "subject_sequence",
        "sseq",
        "subject_alignment",
    )
    subject_interval = _gene_subject_interval(record)
    subject_length = _gene_number(
        _gene_value(
            record,
            "subject_length",
            "slen",
            "target_length",
        )
    )

    if not sequence or subject_interval is None or subject_length is None:
        return False

    if not (subject_interval[0] <= 1 and subject_interval[1] >= subject_length):
        return False

    subject_start = _gene_number(
        _gene_value(
            record,
            "subject_start",
            "sstart",
            "target_start",
        )
    )
    subject_end = _gene_number(
        _gene_value(
            record,
            "subject_end",
            "send",
            "target_end",
        )
    )

    if (
        subject_start is not None
        and subject_end is not None
        and subject_end < subject_start
    ):
        sequence = _gene_reverse_complement(sequence)

    sequence = sequence.replace("-", "").upper()

    return len(sequence) >= 3 and sequence[-3:] in (
        "TAA",
        "TAG",
        "TGA",
    )


def _gene_terminal_aligned_codons(record):
    """Return corresponding terminal reference and query codons.

    The query and subject alignment strings are placed in reference
    orientation. The final three non-gap subject positions are used so that
    the query codon is taken from the corresponding alignment columns.

    Returns:
        tuple: Reference codon and query codon. Either value may be None.
    """
    query_sequence = _gene_sequence(
        record,
        "query_sequence",
        "qseq",
        "query_alignment",
    )
    subject_sequence = _gene_sequence(
        record,
        "subject_sequence",
        "sseq",
        "subject_alignment",
    )

    if not query_sequence or not subject_sequence:
        return None, None

    query_sequence = "".join(query_sequence.split()).upper()
    subject_sequence = "".join(subject_sequence.split()).upper()

    if len(query_sequence) != len(subject_sequence):
        return None, None

    subject_start = _gene_number(
        _gene_value(
            record,
            "subject_start",
            "sstart",
            "target_start",
        )
    )
    subject_end = _gene_number(
        _gene_value(
            record,
            "subject_end",
            "send",
            "target_end",
        )
    )

    if (
        subject_start is not None
        and subject_end is not None
        and subject_end < subject_start
    ):
        query_sequence = _gene_reverse_complement(query_sequence)
        subject_sequence = _gene_reverse_complement(subject_sequence)

    terminal_indexes = []

    for index in range(
        len(subject_sequence) - 1,
        -1,
        -1,
    ):
        if subject_sequence[index] != "-":
            terminal_indexes.append(index)

        if len(terminal_indexes) == 3:
            break

    if len(terminal_indexes) != 3:
        return None, None

    terminal_indexes.reverse()

    reference_codon = "".join(subject_sequence[index] for index in terminal_indexes)
    query_codon = "".join(query_sequence[index] for index in terminal_indexes)

    valid_bases = set("ACGT")

    if not set(reference_codon).issubset(valid_bases):
        reference_codon = None

    if not set(query_codon).issubset(valid_bases):
        query_codon = None

    return reference_codon, query_codon


def _gene_query_key(value):
    """Return a stable query name for grouping reports by strain."""
    value = os.path.basename(str(value or '').strip())
    for suffix in ('.fasta', '.fa', '.fna', '.fas'):
        if value.lower().endswith(suffix):
            value = value[:-len(suffix)]
    return value


def _gene_report_key(value):
    """Return the strain identifier encoded in a GeneSeekr report name."""
    value = os.path.basename(str(value or '').strip())
    for suffix in (
        '_blastn_geneseekr.tsv',
        '_blastn_geneseekr.csv',
        '_tblastx_geneseekr.tsv',
        '_tblastx_geneseekr.csv',
        '_geneseekr.tsv',
        '_geneseekr.csv',
    ):
        if value.lower().endswith(suffix):
            return value[:-len(suffix)]
    return _gene_query_key(value)


def _gene_percent_match(record):
    return _gene_number(_gene_value(
        record, 'percent_match', 'percent_identity', 'identity',
        'percent_id', 'percentidentity'
    ))


def _gene_identity_passes_cutoff(record, cutoff=GENE_MIN_PERCENT_IDENTITY):
    identity = _gene_percent_match(record)
    return identity is None or identity >= cutoff


def _gene_interval(record, start_names, end_names):
    start = _gene_number(_gene_value(record, *start_names))
    end = _gene_number(_gene_value(record, *end_names))
    if start is None or end is None:
        return None
    return min(start, end), max(start, end)


def _gene_subject_interval(record):
    return _gene_interval(
        record, ('subject_start', 'sstart', 'target_start'),
        ('subject_end', 'send', 'target_end')
    )


def _gene_query_interval(record):
    return _gene_interval(
        record, ('query_start', 'qstart', 'contig_start'),
        ('query_end', 'qend', 'contig_end')
    )


def _gene_bitscore(record):
    return _gene_number(_gene_value(
        record, 'bit_score', 'bitscore', 'score'
    )) or 0


def _gene_hit_quality(record):
    subject_interval = _gene_subject_interval(record)
    subject_length = _gene_number(_gene_value(
        record, 'subject_length', 'slen', 'target_length'
    ))
    coverage = 0
    if subject_interval is not None and subject_length:
        coverage = (subject_interval[1] - subject_interval[0] + 1) / subject_length
    return coverage, _gene_percent_match(record) or 0, _gene_bitscore(record)


def _gene_sequence(record, *names):
    value = _gene_value(record, *names)
    return ''.join(str(value or '').upper().split())


def _gene_reverse_complement(sequence):
    complement = str.maketrans(
        'ACGTRYKMSWBDHVN-', 'TGCAYRMKSWVHDBN-'
    )
    return sequence.translate(complement)[::-1]


def _gene_translate(sequence, offset=0):
    table = {
        'TTT': 'F', 'TTC': 'F', 'TTA': 'L', 'TTG': 'L',
        'TCT': 'S', 'TCC': 'S', 'TCA': 'S', 'TCG': 'S',
        'TAT': 'Y', 'TAC': 'Y', 'TAA': '*', 'TAG': '*',
        'TGT': 'C', 'TGC': 'C', 'TGA': '*', 'TGG': 'W',
        'CTT': 'L', 'CTC': 'L', 'CTA': 'L', 'CTG': 'L',
        'CCT': 'P', 'CCC': 'P', 'CCA': 'P', 'CCG': 'P',
        'CAT': 'H', 'CAC': 'H', 'CAA': 'Q', 'CAG': 'Q',
        'CGT': 'R', 'CGC': 'R', 'CGA': 'R', 'CGG': 'R',
        'ATT': 'I', 'ATC': 'I', 'ATA': 'I', 'ATG': 'M',
        'ACT': 'T', 'ACC': 'T', 'ACA': 'T', 'ACG': 'T',
        'AAT': 'N', 'AAC': 'N', 'AAA': 'K', 'AAG': 'K',
        'AGT': 'S', 'AGC': 'S', 'AGA': 'R', 'AGG': 'R',
        'GTT': 'V', 'GTC': 'V', 'GTA': 'V', 'GTG': 'V',
        'GCT': 'A', 'GCC': 'A', 'GCA': 'A', 'GCG': 'A',
        'GAT': 'D', 'GAC': 'D', 'GAA': 'E', 'GAG': 'E',
        'GGT': 'G', 'GGC': 'G', 'GGA': 'G', 'GGG': 'G',
    }
    sequence = ('N' * offset) + sequence.replace('-', '')
    return ''.join(
        table.get(sequence[index:index + 3], 'X')
        for index in range(0, len(sequence) - 2, 3)
    )


def _gene_translation(record):
    """Translate a nucleotide BLAST query in the reference orientation."""
    sequence = _gene_sequence(
        record, 'query_sequence', 'qseq', 'query_alignment'
    )
    subject_interval = _gene_subject_interval(record)
    if not sequence or subject_interval is None:
        return '', None
    subject_start = _gene_number(_gene_value(
        record, 'subject_start', 'sstart', 'target_start'
    ))
    subject_end = _gene_number(_gene_value(
        record, 'subject_end', 'send', 'target_end'
    ))
    if subject_start is not None and subject_end is not None and subject_end < subject_start:
        sequence = _gene_reverse_complement(sequence)
    offset = int(subject_interval[0] - 1) % 3
    return _gene_translate(sequence, offset), subject_interval[1]


def _gene_internal_stop_coordinates(record, protein):
    """Return nucleotide ranges for premature translated stop codons."""
    sequence = _gene_sequence(
        record, 'query_sequence', 'qseq', 'query_alignment'
    ).replace('-', '')
    if not sequence or not protein:
        return []
    subject_start = _gene_number(_gene_value(
        record, 'subject_start', 'sstart', 'target_start'
    ))
    subject_end = _gene_number(_gene_value(
        record, 'subject_end', 'send', 'target_end'
    ))
    offset = 0
    subject_interval = _gene_subject_interval(record)
    if subject_interval is not None:
        offset = int(subject_interval[0] - 1) % 3
    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    if query_start is None or query_end is None:
        return []
    reverse = query_end < query_start
    coordinates = []
    def coordinate_text(value):
        return str(int(value)) if int(value) == value else str(value)

    for amino_acid_index, amino_acid in enumerate(protein[:-1]):
        if amino_acid != '*':
            continue
        sequence_index = offset + (amino_acid_index * 3)
        if reverse:
            codon_end = max(query_start, query_end) - sequence_index
            coordinates.append('{0}-{1}'.format(
                coordinate_text(codon_end - 2), coordinate_text(codon_end)
            ))
        else:
            codon_start = min(query_start, query_end) + sequence_index
            coordinates.append('{0}-{1}'.format(
                coordinate_text(codon_start), coordinate_text(codon_start + 2)
            ))
    return coordinates


def _gene_internal_stop_evidence(record, protein):
    """Return query/reference coordinates and codons for internal stops."""
    sequence = _gene_sequence(
        record, 'query_sequence', 'qseq', 'query_alignment'
    ).replace('-', '')
    subject_sequence = _gene_sequence(
        record, 'subject_sequence', 'sseq', 'subject_alignment'
    ).replace('-', '')
    if not sequence or not protein:
        return []

    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    subject_start = _gene_number(_gene_value(
        record, 'subject_start', 'sstart', 'target_start'
    ))
    subject_end = _gene_number(_gene_value(
        record, 'subject_end', 'send', 'target_end'
    ))
    if None in (query_start, query_end, subject_start, subject_end):
        return []

    reverse = query_end < query_start
    if reverse:
        sequence = _gene_reverse_complement(sequence)
    if subject_end < subject_start and subject_sequence:
        subject_sequence = _gene_reverse_complement(subject_sequence)

    subject_interval = _gene_subject_interval(record)
    offset = int(subject_interval[0] - 1) % 3 if subject_interval else 0

    def coordinate_text(value):
        return str(int(value)) if int(value) == value else str(value)

    evidence = []
    for amino_acid_index, amino_acid in enumerate(protein[:-1]):
        if amino_acid != '*':
            continue
        sequence_index = offset + (amino_acid_index * 3)
        query_codon = sequence[sequence_index:sequence_index + 3]
        subject_codon = subject_sequence[
            sequence_index:sequence_index + 3
        ] if subject_sequence else ''
        if reverse:
            query_codon_end = max(query_start, query_end) - sequence_index
            query_location = '{0}-{1}'.format(
                coordinate_text(query_codon_end - 2),
                coordinate_text(query_codon_end),
            )
        else:
            query_codon_start = min(query_start, query_end) + sequence_index
            query_location = '{0}-{1}'.format(
                coordinate_text(query_codon_start),
                coordinate_text(query_codon_start + 2),
            )
        subject_codon_start = (
            min(subject_start, subject_end) + sequence_index
        )
        subject_location = '{0}-{1}'.format(
            coordinate_text(subject_codon_start),
            coordinate_text(subject_codon_start + 2),
        )
        item = {
            'query_location': query_location,
            'subject_location': subject_location,
        }
        if query_codon and subject_codon:
            item['codon_change'] = '{0} -> {1}'.format(
                subject_codon, query_codon
            )
        evidence.append(item)
    return evidence


def _gene_alignment_gap(first, second):
    """Return the gap between two query intervals, including overlaps."""
    first_interval = _gene_interval(
        first, ('query_start', 'qstart', 'contig_start'),
        ('query_end', 'qend', 'contig_end')
    )
    second_interval = _gene_interval(
        second, ('query_start', 'qstart', 'contig_start'),
        ('query_end', 'qend', 'contig_end')
    )
    if first_interval is None or second_interval is None:
        return None
    if first_interval[1] < second_interval[0]:
        return second_interval[0] - first_interval[1]
    if second_interval[1] < first_interval[0]:
        return first_interval[0] - second_interval[1]
    return 0


def _gene_at_contig_end(record):
    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    query_length = _gene_number(_gene_value(
        record, 'query_length', 'qlen', 'contig_length'
    ))
    return query_length is not None and (
        (query_start is not None and query_start <= 3) or
        (query_end is not None and query_length - query_end < 3)
    )


def _gene_query_is_reverse(record):
    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    if query_start is None or query_end is None:
        return None
    return query_end < query_start


def _gene_alignment_frame(record):
    """Return the query/reference phase for a nucleotide alignment."""
    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    subject_start = _gene_number(_gene_value(
        record, 'subject_start', 'sstart', 'target_start'
    ))
    subject_end = _gene_number(_gene_value(
        record, 'subject_end', 'send', 'target_end'
    ))
    if None in (query_start, query_end, subject_start, subject_end):
        return None
    query_anchor = query_start if query_end >= query_start else query_end
    subject_anchor = subject_start if subject_end >= subject_start else subject_end
    return int((query_anchor - subject_anchor) % 3)


def _gene_subject_gap(first, second):
    """Return the unaligned gap between disjoint reference intervals."""
    first_interval = _gene_subject_interval(first)
    second_interval = _gene_subject_interval(second)
    if first_interval is None or second_interval is None:
        return None
    if first_interval[1] < second_interval[0]:
        return second_interval[0] - first_interval[1] - 1
    if second_interval[1] < first_interval[0]:
        return first_interval[0] - second_interval[1] - 1
    # Overlapping alignments are not consecutive reference fragments. They
    # commonly occur when BLAST reports a shorter and a full-length hit.
    return None


def _gene_detail_hit(record):
    subject_interval = _gene_subject_interval(record)
    query_interval = _gene_query_interval(record)
    subject_length = _gene_number(_gene_value(
        record, 'subject_length', 'slen', 'target_length'
    ))
    return {
        'query': _gene_query_key(_gene_value(
            record, 'query', 'Query', 'query_id', 'qseqid'
        )),
        'target': str(_gene_value(
            record, 'target', 'Target', 'subject_id', 'sseqid'
        ) or ''),
        'identity': _gene_percent_match(record),
        'coverage': round(_gene_hit_quality(record)[0] * 100, 2),
        'subject_start': subject_interval[0] if subject_interval else None,
        'subject_end': subject_interval[1] if subject_interval else None,
        'subject_length': subject_length,
        'query_start': query_interval[0] if query_interval else None,
        'query_end': query_interval[1] if query_interval else None,
        'orientation': 'reverse' if _gene_query_is_reverse(record) else 'forward',
        'bit_score': _gene_bitscore(record),
    }


def _is_salmonella_gene_target(target):
    """Return whether a gene reference is invA or stn."""
    normalized = str(target or '').strip().lower()
    return (
        normalized == 'inva' or normalized.endswith('_inva') or
        normalized == 'stn' or normalized.endswith('_stn') or
        'm90846' in normalized or 'l16014' in normalized
    )


def _gene_target_allowed(target, allowed_targets):
    normalized = str(target or '').strip().lower()
    if (
        set(str(allowed).strip().lower() for allowed in allowed_targets) ==
        set(('inva', 'stn'))
    ):
        return _is_salmonella_gene_target(target)
    return any(
        normalized == str(allowed).strip().lower() or
        normalized.endswith('_' + str(allowed).strip().lower())
        for allowed in allowed_targets
    )


def categorize_gene_hit_details(records, identity_cutoff=90,
                                allowed_targets=None):
    """Return gene-specific evidence for each assigned category."""
    grouped = {}
    for record in records or []:
        query = _gene_query_key(_gene_value(
            record, 'query', 'Query', 'query_id', 'qseqid'
        ))
        if query and (
            identity_cutoff is None or
            _gene_identity_passes_cutoff(record, identity_cutoff)):
            report = _gene_report_key(_gene_value(record, 'report'))
            grouped.setdefault(report or query, {}).setdefault(
                query, []
            ).append(record)

    details = {}
    for report, query_groups in grouped.items():
        targets = {}
        for query, hits in query_groups.items():
            for hit in hits:
                target = str(_gene_value(
                    hit, 'target', 'Target', 'subject_id', 'sseqid'
                ) or '').strip()
                if not target:
                    continue
                if (
                    allowed_targets is not None and
                    not _gene_target_allowed(target, allowed_targets)
                ):
                    continue
                targets.setdefault(target, {}).setdefault(
                    query, []
                ).append(hit)

        report_details = {}
        for target, contig_hits in targets.items():
            best_by_contig = {
                query: max(hits, key=_gene_hit_quality)
                for query, hits in contig_hits.items()
            }
            best_hit = max(
                best_by_contig.values(), key=_gene_hit_quality
            )
            protein, subject_end = _gene_translation(best_hit)
            subject_interval = _gene_subject_interval(best_hit)
            subject_length = _gene_number(_gene_value(
                best_hit,
                'subject_length',
                'slen',
                'target_length'
            ))

            terminal_reference = (
                subject_interval is not None and
                subject_length is not None and
                subject_interval[0] <= 1 and
                subject_end is not None and
                subject_end >= subject_length
            )

            evidence = []

            has_internal_stop = bool(
                protein and (
                    '*' in protein[:-1] or
                    (
                        protein.endswith('*') and
                        not terminal_reference
                    )
                )
            )

            if has_internal_stop:
                stop_coordinates = _gene_internal_stop_coordinates(
                    best_hit,
                    protein
                )
                stop_evidence = _gene_internal_stop_evidence(
                    best_hit,
                    protein
                )
                evidence.append({
                    'category': 'INTERNAL_STOP',
                    'reason': (
                        'Internal stop codon(s) encountered at nucleotide '
                        '{0}.'.format(', '.join(stop_coordinates))
                        if stop_coordinates else
                        'Translated query contains a premature stop codon.'
                    ),
                    'hit': _gene_detail_hit(best_hit),
                    'terminal_reference': terminal_reference,
                    'stop_coordinates': stop_coordinates,
                    'stop_evidence': stop_evidence
                })

            reference_terminal_codon, query_terminal_codon = (
                _gene_terminal_aligned_codons(best_hit)
            )
            reference_has_terminal_stop = (
                reference_terminal_codon in ('TAA', 'TAG', 'TGA')
            )
            query_has_terminal_stop = (
                query_terminal_codon in ('TAA', 'TAG', 'TGA')
            )

            if (
                terminal_reference and
                reference_has_terminal_stop and
                query_terminal_codon is not None and
                not query_has_terminal_stop
            ):
                evidence.append({
                    'category': 'EXTENDED',
                    'reason': (
                        'The complete reference ends in stop codon {0}, but '
                        'the corresponding query codon is {1}.'.format(
                            reference_terminal_codon,
                            query_terminal_codon
                        )
                    ),
                    'hit': _gene_detail_hit(best_hit),
                    'terminal_reference': True,
                    'reference_terminal_codon': (
                        reference_terminal_codon
                    ),
                    'query_terminal_codon': query_terminal_codon
                })

            for hit in best_by_contig.values():
                hit_interval = _gene_subject_interval(hit)
                hit_subject_length = _gene_number(_gene_value(
                    hit,
                    'subject_length',
                    'slen',
                    'target_length'
                ))
                if (
                    hit_interval is not None and
                    hit_subject_length is not None and
                    hit_interval[1] < hit_subject_length and
                    _gene_at_contig_end(hit)
                ):
                    evidence.append({
                        'category': 'PARTIAL_CONTIG_END',
                        'reason': (
                            'Reference hit ends before the target terminus '
                            'at a contig boundary.'
                        ),
                        'hit': _gene_detail_hit(hit)
                    })

            contig_hits_list = list(best_by_contig.values())
            for index, first in enumerate(contig_hits_list):
                for second in contig_hits_list[index + 1:]:
                    gap = _gene_subject_gap(first, second)
                    if (
                        gap is not None and gap < 10 and
                        _gene_query_is_reverse(first) is not None and
                        _gene_query_is_reverse(first) ==
                        _gene_query_is_reverse(second) and
                        _gene_alignment_frame(first) is not None and
                        _gene_alignment_frame(second) is not None and
                        _gene_alignment_frame(first) !=
                        _gene_alignment_frame(second)
                    ):
                        evidence.append({
                            'category': 'FRAME_SHIFT',
                            'reason': (
                                'Adjacent reference fragments have different '
                                'inferred reading frames.'
                            ),
                            'gap': gap,
                            'first_hit': _gene_detail_hit(first),
                            'second_hit': _gene_detail_hit(second)
                        })

            if evidence:
                report_details[target] = evidence

        if report_details:
            details[report] = report_details

    return details


def categorize_gene_hits(records, allowed_targets=None, identity_cutoff=90):
    """Return the assigned GeneSeekr categories for each report.

    Detailed classification is performed by
    ``categorize_gene_hit_details`` so that the category summary and
    supporting evidence always use identical rules.

    Args:
        records (list): Normalized GeneSeekr or BLAST result records.
        allowed_targets (iterable): Optional target-name restriction.
        identity_cutoff (float): Minimum identity percentage.

    Returns:
        dict: Report names mapped to sorted category lists.
    """
    details = categorize_gene_hit_details(
        records=records,
        identity_cutoff=identity_cutoff,
        allowed_targets=allowed_targets,
    )

    result = {}

    for report, targets in details.items():
        categories = set()

        for evidence_items in targets.values():
            for evidence in evidence_items:
                category = evidence.get("category")

                if category:
                    categories.add(category)

        if categories:
            result[report] = sorted(categories)

    return result


def filter_report_by_gene_categories(report_text, categories, selected):
    """Remove categorized strains from the JSON report used for summaries."""
    try:
        report = json.loads(report_text or '{}')
    except (TypeError, ValueError):
        return report_text
    selected = set(selected or [])
    excluded = {
        query for query, values in (categories or {}).items()
        if selected.intersection(values)
    }
    if not excluded:
        return report_text

    def matches(seqid, query):
        seqid = _gene_query_key(seqid).lower()
        query = _gene_query_key(query).lower()
        return seqid == query or seqid in query or query in seqid

    for primer_data in report.values():
        if not isinstance(primer_data, dict):
            continue
        for panel_data in primer_data.values():
            if not isinstance(panel_data, dict):
                continue
            for seqid in list(panel_data):
                if any(matches(seqid, query) for query in excluded):
                    panel_data.pop(seqid, None)
    return json.dumps(report, sort_keys=True, indent=4, separators=(',', ': '))


def gene_exclusion_note(selected, categories):
    """Build the persisted explanation shown with filtered summaries."""
    selected = sorted(
        set(selected or []), key=lambda value: str(value or '')
    )
    excluded = sorted(
        [
            query for query, values in (categories or {}).items()
            if set(values).intersection(selected)
        ],
        key=lambda value: str(value or '')
    )
    if not selected or not excluded:
        return ''
    return (
        'Gene-analysis exclusions: {0} categor{1} selected; {2} strain{3} '
        'removed from summary totals and model results. Categorized strains '
        'remain in the Excel report.'.format(
            ', '.join(selected),
            'y' if len(selected) == 1 else 'ies',
            len(excluded),
            '' if len(excluded) == 1 else 's',
        )
    )


def _validator_classification_label(found_targets, required_targets, logic,
                                    classification_rule):
    """Apply classification precedence, then generic target logic."""
    if classification_rule == 'salmonella':
        return (
            'Salmonella'
            if set(found_targets).intersection(('inva', 'stn'))
            else 'Not Salmonella'
        )
    if logic == 'AND':
        passes = found_targets == required_targets
    elif logic == 'OR':
        passes = bool(found_targets)
    else:
        return None
    return 'Detected' if passes else 'Not detected'


def _validator_logic_label(found_targets, required_targets, logic):
    """Return the human-readable result for the configured target logic."""
    if logic == 'AND' and found_targets == required_targets:
        return 'Pass: All targets matched'
    if found_targets and logic == 'AND':
        return 'Fail: At least one target matched'
    if found_targets and logic == 'OR':
        return 'Pass: At least one target matched'
    if logic in ('AND', 'OR'):
        return 'Fail: No targets matched'
    return ''


def create_details(
    *,  # Ensure that the following arguments are keyword arguments
    panel_model: Type[Union[VerifierPanel, ValidatorPanel]],
    primer_set_model: Type[Union[VerifierPrimerSet, ValidatorPrimerSet]],
    request: Type[Union[VerifierRequest, ValidatorRequest]],
    analysis: str = 'verifier'
) -> Type[Union[VerifierRequest, ValidatorRequest]]:
    """
    Parses the report to extract panel- and genus-specific details.

    Args:
        panel_model: Panel model for the current analysis.
        primer_set_model: PrimerSet model for the current analysis.
        request: Request object.
        analysis: Type of analysis. Default is verifier.

    Returns:
        The updated VerifierRequest object.
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

        retrieve_panel_seqids(
            genus=genus,
            run_folder=run_folder,
            seq_dictionary=seq_dict,
        )
    seq_dict_by_genus = seq_dict
    # There's apparently some issue with the Listeria/VTEC benchmark dataset,
    # as there is a duplicate seqid in the Listeria dataset. This will be
    # fixed in the future, but for now, we will just report any duplicates
    find_shared_seqids(seq_dictionary=seq_dict_by_genus)

    probe_configuration = {}
    if isinstance(request, ValidatorRequest):
        for configured_sample in request.samples.prefetch_related('targets'):
            for configured_target in configured_sample.targets.all():
                probe_configuration[
                    configured_target.name.strip().lower()
                ] = configured_target

    target_probes_configured = any(
        (getattr(target, 'probe_sequence', '') or '').strip()
        for target in probe_configuration.values()
    )
    request_level_probe = (
        bool((getattr(request, 'probe_sequence', '') or '').strip())
        and not target_probes_configured
    )

    def probe_is_required(primer_name):
        """Return whether this primer target requires a probe match."""
        configured_target = probe_configuration.get(
            str(primer_name or '').strip().lower()
        )
        target_probe = bool(
            configured_target is not None and
            (getattr(configured_target, 'probe_sequence', '') or '').strip()
        )
        return request_level_probe or target_probe

    def probe_passes(sequence_data, primer_name):
        """Apply the configured probe rule; missing hits do not pass."""
        probe_data = (
            sequence_data.get('probe')
            if isinstance(sequence_data, dict)
            else None
        )
        if not isinstance(probe_data, dict):
            return False

        probe_info = probe_data.get('probe', probe_data)
        if not isinstance(probe_info, dict):
            return False

        configured_target = probe_configuration.get(
            str(primer_name or '').strip().lower()
        )
        method = getattr(
            configured_target,
            'probe_evaluation_method',
            'percent_identity'
        )
        percent_threshold = float(getattr(
            configured_target,
            'probe_percent_identity',
            80.0
        ))
        difference_threshold = int(getattr(
            configured_target,
            'probe_max_differences',
            2
        ))

        percent_id = probe_info.get(
            'percent_id',
            probe_info.get('percent_identity')
        )
        gaps = probe_info.get('gaps', probe_info.get('gap'))
        mismatches = probe_info.get(
            'mismatches',
            probe_info.get('mismatch')
        )

        try:
            observed_identity = float(percent_id)
        except (TypeError, ValueError):
            observed_identity = None

        try:
            observed_differences = (
                int(float(mismatches)) + int(float(gaps))
            )
        except (TypeError, ValueError):
            observed_differences = None

        if method == 'max_differences':
            return (
                observed_differences is not None and
                observed_differences <= difference_threshold
            )

        return (
            observed_identity is not None and
            observed_identity >= percent_threshold
        )

    for primer in primer_set:
        primer_name = primer.primer_name
        primer_requires_probe = probe_is_required(primer_name)
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
            seqids = seq_dict_by_genus.get(genus, [])

            # Calculate genus-specific total
            panel_total = 0
            panel_positive = 0

            total_dict = {}

            # Probe prevalence. When a probe is configured, every included
            # panel sequence contributes to the denominator. Missing probe
            # output is therefore counted as a negative result.
            probe_positive = 0

            # Iterate over a copy of the seqids dictionary
            for seqid in seqids:

                if analysis == 'validator':
                    excluded = any(
                        set(values).intersection(
                            request.gene_exclusion_categories or []
                        ) and (
                            _gene_query_key(seqid).lower() in
                            _gene_query_key(query).lower() or
                            _gene_query_key(query).lower() in
                            _gene_query_key(seqid).lower()
                        )
                        for query, values in
                        (request.gene_exclusion_results or {}).items()
                    )
                    if excluded:
                        continue

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

                probe_hit = (
                    probe_passes(sequence_data, primer_name)
                    if primer_requires_probe
                    else False
                )

                if hit and (not primer_requires_probe or probe_hit):
                    # A configured probe is part of the detection rule. An
                    # amplicon with no qualifying probe match is Not Detected,
                    # matching the per-row result table and Excel output.
                    panel_positive += 1
                    total_dict.update(totals)

                if primer_requires_probe and probe_hit:
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

            # Store probe prevalence whenever this target requires a probe.
            # The denominator is the full included panel, so absent/no-match
            # probe records are represented as negatives rather than omitted.
            if primer_requires_probe:
                try:
                    probe_percent = float(
                        "{:.2f}".format(probe_positive / panel_total * 100)
                    )
                except ZeroDivisionError:
                    probe_percent = 0.00
                details[primer_name][panel_name][genus]["probe_prevalence"] = {
                    "percent": probe_percent,
                    "positive": probe_positive,
                    "negative": panel_total - probe_positive,
                    "total": panel_total,
                }

    sort_mismatch_details(
        details=details
    )

    request.summary = json.dumps(
        dict(
            details,
            _metadata={
                'benchmark_versions': request.benchmark_versions or {},
            },
        ), sort_keys=True, indent=4, separators=(',', ': '))
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
            genera_str = ','.join(
                sorted(genera, key=lambda value: str(value or ''))
            )
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


def _upload_summary_excel(blob_client, container_name, request):
    """
    Create a consolidated Excel report and upload it to Azure Blob Storage.

    The results worksheet contains one row per panel/SEQID combination and
    one group of result columns per primer set. Mismatch-detail cells contain
    a three-line, BLAST-like pseudoalignment whenever primer sequences are
    available from the related primer model.
    """
    if xlsxwriter is None:
        return

    report_text = getattr(request, "validator_original_report", '') or getattr(
        request, "report", None
    )
    if not report_text:
        return

    try:
        report = json.loads(report_text)
    except (TypeError, ValueError):
        return

    if not isinstance(report, dict):
        return

    primer_fields = [
        ("amplicon length", "amplicon_length"),
        ("contig", "contig"),
        ("location", "location"),
        ("direction", "direction"),
        ("forward mismatch", "forward_mismatch"),
        ("forward mismatch details", "forward_mismatch_details"),
        ("reverse mismatch", "reverse_mismatch"),
        ("reverse mismatch details", "reverse_mismatch_details"),
        ("Total Mismatches", "total_mismatch"),
    ]

    probe_fields = (
        ("Probe Presence", "probe_presence"),
        ("Probe Gaps", "probe_gaps"),
        ("Probe Mismatches", "probe_mismatches"),
        ("Probe Mismatch Details", "probe_mismatch_details"),
        ("Probe Evaluation Method", "probe_evaluation_method"),
        ("Probe Evaluation Threshold", "probe_evaluation_threshold"),
        ("Probe Percent Identity", "probe_percent_identity"),
        ("Probe Total Differences", "probe_total_differences"),
)

    has_probe = bool((getattr(request, "probe_sequence", "") or "").strip())
    if isinstance(request, ValidatorRequest):
        has_probe = has_probe or any(
            (target.probe_sequence or '').strip()
            for sample in request.samples.prefetch_related('targets')
            for target in sample.targets.all()
        )

    def _get_field(data, names, default=None):
        """Return the first available report field."""
        if not isinstance(data, dict):
            return default
        for name in names:
            if name in data:
                return data.get(name)
        return default

    def _clean_sequence(value):
        """Remove formatting whitespace from a sequence string."""
        return "".join(str(value or "").split()).upper()

    def _decode_details(value):
        """Decode HTML entities such as ``&gt;`` in mismatch details."""
        try:
            from html import unescape
        except ImportError:
            from HTMLParser import HTMLParser

            unescape = HTMLParser().unescape
        return unescape(str(value or ""))

    def _primer_direction(header):
        """Determine whether a primer header is forward or reverse."""
        header = str(header or "").strip().lower()
        forward_suffixes = (
            "-f",
            "_f",
            ".f",
            " forward",
            "_forward",
        )
        reverse_suffixes = (
            "-r",
            "_r",
            ".r",
            " reverse",
            "_reverse",
        )

        if header.endswith(forward_suffixes):
            return "forward"
        if header.endswith(reverse_suffixes):
            return "reverse"
        return None

    def _target_from_details(primer_sequence, details):
        """
        Reconstruct a target sequence from details such as ``2T>G;17G>A``.

        Positions are treated as one-based. The base following ``>`` is
        placed in the target sequence. With no mismatches, target and primer
        are identical, allowing a complete match-line to be displayed.
        """
        import re

        primer_sequence = _clean_sequence(primer_sequence)
        if not primer_sequence:
            return ""

        target = list(primer_sequence)
        detail_text = _decode_details(details)
        substitutions = re.findall(
            r"(\d+)\s*([A-Za-z-])\s*>\s*([A-Za-z-])",
            detail_text,
        )

        for position, primer_base, target_base in substitutions:
            del primer_base
            index = int(position) - 1
            if 0 <= index < len(target):
                target[index] = target_base.upper()

        return "".join(target)

    def _pseudoalignment(primer_sequence, target_sequence, details):
        """Return a three-line, BLAST-like primer/target alignment."""
        primer_sequence = _clean_sequence(primer_sequence)
        target_sequence = _clean_sequence(target_sequence)

        if not target_sequence:
            target_sequence = _target_from_details(
                primer_sequence,
                details,
            )

        if not primer_sequence or not target_sequence:
            return _decode_details(details) or "ND"

        length = max(len(primer_sequence), len(target_sequence))
        primer_sequence = primer_sequence.ljust(length, "-")
        target_sequence = target_sequence.ljust(length, "-")
        match_line = "".join(
            "|" if primer_base == target_base else " "
            for primer_base, target_base in zip(
                primer_sequence,
                target_sequence,
            )
        )

        return "Primer: {0}\n        {1}\nTarget: {2}".format(
            primer_sequence,
            match_line,
            target_sequence,
        )

    def _combined_pseudoalignment(
        forward_query,
        forward_target,
        forward_details,
        reverse_query,
        reverse_target,
        reverse_details,
    ):
        """Return forward and reverse pseudoalignments in one Excel cell."""
        forward_alignment = _pseudoalignment(
            forward_query,
            forward_target,
            forward_details,
        )
        reverse_alignment = _pseudoalignment(
            reverse_query,
            reverse_target,
            reverse_details,
        )
        return "FORWARD\n{0}\n\nREVERSE\n{1}".format(
            forward_alignment,
            reverse_alignment,
        )

    def _parse_mismatch(value):
        """Convert a mismatch value to an integer when possible."""
        if value in (None, "", "ND"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

    probe_configuration = {}
    if isinstance(request, ValidatorRequest):
        for configured_sample in request.samples.prefetch_related('targets'):
            for configured_target in configured_sample.targets.all():
                probe_configuration[
                    configured_target.name.strip().lower()
                ] = configured_target

    def _probe_values(data, primer_name=None):
        """Return probe alignment values and the configured pass decision."""
        probe_data = data.get('probe') if isinstance(data, dict) else None
        if not isinstance(probe_data, dict):
            return {
                'probe_presence': 'Absent',
                'probe_gaps': 'ND',
                'probe_mismatches': 'ND',
                'probe_mismatch_details': 'ND',
                'probe_evaluation_method': 'ND',
                'probe_evaluation_threshold': 'ND',
                'probe_percent_identity': 'ND',
                'probe_total_differences': 'ND',
            }
        probe_info = probe_data.get('probe', probe_data)
        if not isinstance(probe_info, dict):
            probe_info = {}
        percent_id = _get_field(
            probe_info, ['percent_id', 'percent_identity'], 0
        )
        gaps = _get_field(probe_info, ['gaps', 'gap'], 'ND')
        mismatches = _get_field(
            probe_info, ['mismatches', 'mismatch'], 'ND'
        )
        configured_target = probe_configuration.get(
            str(primer_name or '').strip().lower()
        )
        method = getattr(
            configured_target, 'probe_evaluation_method', 'percent_identity'
        )
        percent_threshold = float(getattr(
            configured_target, 'probe_percent_identity', 80.0
        ))
        difference_threshold = int(getattr(
            configured_target, 'probe_max_differences', 2
        ))
        try:
            observed_identity = float(percent_id)
        except (TypeError, ValueError):
            observed_identity = None
        try:
            observed_differences = int(float(mismatches)) + int(float(gaps))
        except (TypeError, ValueError):
            observed_differences = None

        if method == 'max_differences':
            present = (
                observed_differences is not None and
                observed_differences <= difference_threshold
            )
            threshold = difference_threshold
        else:
            method = 'percent_identity'
            present = (
                observed_identity is not None and
                observed_identity >= percent_threshold
            )
            threshold = percent_threshold

        result = {
            "probe_presence": "Present" if present else "Absent",
            "probe_gaps": gaps,
            "probe_mismatches": mismatches,
            "probe_evaluation_method": method,
            "probe_evaluation_threshold": threshold,
            "probe_percent_identity": (
                round(observed_identity, 2) if observed_identity is not None else "ND"
            ),
            "probe_total_differences": (
                observed_differences if observed_differences is not None else "ND"
            ),
            "probe_mismatch_details": _decode_details(
                _get_field(probe_info, ["mismatch_details"], "")
            )
            or "ND",
        }
        return result

    def _location(data, model_object):
        """Return the explicit location or construct start-stop coordinates."""
        value = _get_field(
            data,
            ["location", "amplicon_range", "GenomeLocation"],
        )
        if value not in (None, ""):
            return value

        start = _get_field(data, ["start_pos"])
        stop = _get_field(data, ["stop_pos"])
        if model_object is not None:
            start = start or model_object.start_pos
            stop = stop or model_object.stop_pos

        if start not in (None, "") and stop not in (None, ""):
            return "{0}-{1}".format(start, stop)
        return "ND"

    # Collect one worksheet entry per panel/SEQID and retain per-primer data.
    entries = {}
    primer_names = set()

    for primer_name, primer_data in report.items():
        if primer_name == "validator_version":
            continue
        if not isinstance(primer_data, dict):
            continue
        primer_name = str(primer_name or '').strip()
        if not primer_name:
            continue

        primer_names.add(primer_name)
        for panel_name, panel_data in primer_data.items():
            if not isinstance(panel_data, dict):
                continue

            panel_name = str(panel_name).upper()
            for seqid, sequence_data in panel_data.items():
                if seqid in ("percent", "positive", "total"):
                    continue
                seqid = str(seqid or '').strip()
                if not seqid:
                    continue

                entry = entries.setdefault(
                    (panel_name, seqid),
                    {"per_primer": {}},
                )
                entry["per_primer"][primer_name] = (
                    sequence_data if isinstance(sequence_data, dict) else {}
                )

    primer_names = sorted(
        primer_names, key=lambda value: str(value or '')
    )
    gene_targets = primer_names[:]

    selected_categories = set(
        getattr(request, 'gene_exclusion_categories', None) or []
    )
    has_gene_references = False
    if isinstance(request, ValidatorRequest):
        has_gene_references = any(
            (target.gene_sequence or '').strip() or target.reference_fasta
            for sample in request.samples.prefetch_related('targets')
            for target in sample.targets.all()
        )
    include_gene_categories = bool(
        has_gene_references and selected_categories
    )

    def gene_label(reference):
        """Map GeneSeekr reference IDs to configured primer/gene names."""
        reference = str(reference or '').lower()
        for gene in gene_targets:
            normalized = gene.strip().lower()
            if reference == normalized or reference.endswith('_' + normalized):
                return gene
        if 'm90846' in reference:
            return next(
                (gene for gene in gene_targets
                 if gene.strip().lower() == 'inva'),
                'invA',
            )
        if 'l16014' in reference or reference.endswith('_stn'):
            return next(
                (gene for gene in gene_targets
                 if gene.strip().lower() == 'stn'),
                'stn',
            )
        return str(reference)

    gene_percent_matches = {}
    if has_gene_references and isinstance(request, ValidatorRequest):
        for record in request.gene_results or []:
            report_key = _gene_report_key(_gene_value(record, 'report')).lower()
            target_name = gene_label(_gene_value(
                record, 'target', 'Target', 'subject_id', 'sseqid'
            )).strip().lower()
            identity = _gene_percent_match(record)
            if report_key and target_name and identity is not None:
                key = (report_key, target_name)
                gene_percent_matches[key] = max(
                    identity, gene_percent_matches.get(key, identity)
                )

    # Load model data because query/ref strings and primer sequences may not
    # be included in request.report.
    if isinstance(request, VerifierRequest):
        sequence_model = VerifierSEQID
        primer_set_model = VerifierPrimerSet
        request_filter = "panel__verifier_request_id"
        primer_request_filter = "verifier_request_id"
    else:
        sequence_model = ValidatorSEQID
        primer_set_model = ValidatorPrimerSet
        request_filter = "panel__validator_request_id"
        primer_request_filter = "validator_request_id"

    from olc_webportalv2.metadata.models import SequenceData
    from olc_webportalv2.common.benchmarks import BENCHMARK_DATASETS
    metadata_by_seqid = {
        metadata.seqid: metadata
        for metadata in SequenceData.objects.filter(
            seqid__in=[entry_key[1] for entry_key in entries]
        ).select_related('labid')
    }

    # The PrimerValidator workbook is the authoritative source for the
    # benchmark strain columns. Keep database metadata as a fallback for
    # processed sequences that are not present in the workbook exports.
    metadata_directory = os.path.join(os.path.dirname(__file__), 'metadata')
    metadata_files = (
        ('inclusivity', 'bds-salmonella.csv'),
        ('exclusivity', 'bds-exclusivity.csv'),
        ('inclusivity', 'ncbi-salmonella.csv'),
    )
    entry_seqids = set(entry_key[1] for entry_key in entries)
    metadata_by_panel_seqid = {}
    for metadata_panel, metadata_filename in metadata_files:
        metadata_path = os.path.join(metadata_directory, metadata_filename)
        if not os.path.exists(metadata_path):
            continue
        with open(metadata_path, 'r', encoding='utf-8') as metadata_file:
            for metadata_row in csv.DictReader(metadata_file):
                seqid = metadata_row.get('seqid', '').strip()
                if seqid and seqid in entry_seqids:
                    metadata_by_panel_seqid[(metadata_panel, seqid)] = (
                        metadata_row
                    )

    sequence_query = (
        sequence_model.objects.filter(**{request_filter: request.pk})
        .select_related(
            "panel",
            "primer",
        )
        .prefetch_related("primer__primer")
    )

    model_values = {}
    benchmark_panel_by_row = {}
    benchmark_labels = dict(BENCHMARK_DATASETS)

    for sequence_object in sequence_query:
        if sequence_object.primer is None:
            continue

        primer_name = sequence_object.primer.primer_name
        model_key = (
            str(sequence_object.panel.panel).upper(),
            sequence_object.seqid,
            primer_name,
        )
        model_values[model_key] = sequence_object
        benchmark_panel_by_row.setdefault(
            (str(sequence_object.panel.panel).upper(), sequence_object.seqid),
            sequence_object.panel.genus,
        )

    validator_classification_by_row = {}
    validator_logic_by_row = {}
    validator_logic_applied = False
    validator_generic_result_by_row = {}
    if isinstance(request, ValidatorRequest):
        configured_samples = list(request.samples.prefetch_related('targets'))
        salmonella_rules = [
            sample for sample in configured_samples
            if sample.classification_rule == 'salmonella'
        ]
        if salmonella_rules:
            classification_targets = set(
                target.name.strip().lower()
                for sample in salmonella_rules
                for target in sample.targets.all()
            )
            classification_targets.intersection_update(set(('inva', 'stn')))
            required_targets = classification_targets
            logic = ''
        else:
            required_targets = set(
                target.name.strip().lower()
                for sample in configured_samples
                for target in sample.targets.all()
            )
            logic_values = set(
                sample.logic for sample in configured_samples
                if sample.logic in ('AND', 'OR')
            )
            logic = next(iter(logic_values), '') if len(logic_values) == 1 else ''
            validator_logic_applied = bool(logic)

        if required_targets and (salmonella_rules or logic):
            probe_required_targets = set()

            if isinstance(request, ValidatorRequest):
                for sample in configured_samples:
                    for target in sample.targets.all():
                        if (target.probe_sequence or '').strip():
                            probe_required_targets.add(
                                target.name.strip().lower()
                            )

            # The legacy request-level probe applies to every configured
            # target because it is submitted as the shared probe sequence.
            if (
                (getattr(request, 'probe_sequence', '') or '').strip()
                and not probe_required_targets
            ):
                probe_required_targets.update(required_targets)

            for panel_name, seqid in entries:
                found_targets = set()
                per_primer = entries[(panel_name, seqid)]['per_primer']

                for target_name in required_targets:
                    primer_name = next(
                        (
                            name for name in per_primer
                            if name.strip().lower() == target_name
                        ),
                        target_name
                    )
                    model_object = model_values.get(
                        (panel_name, seqid, primer_name)
                    )
                    report_data = per_primer.get(primer_name, {})
                    amplicon_length = _get_field(
                        report_data,
                        ('amplicon_length', 'amplicon_size', 'AmpliconSize'),
                        getattr(model_object, 'amplicon_length', '')
                    )
                    amplicon_detected = (
                        str(amplicon_length or '').strip().lower() not in (
                            '', 'nd', 'none', 'null', '0'
                        )
                    )

                    if not amplicon_detected:
                        continue

                    if target_name in probe_required_targets:
                        probe_values = _probe_values(report_data, primer_name)
                        probe_detected = (
                            str(probe_values.get('probe_presence') or '')
                            .strip().lower() == 'present'
                        )
                        if not probe_detected:
                            continue

                    found_targets.add(target_name)

                row_key = (panel_name, seqid)
                if salmonella_rules:
                    validator_classification_by_row[row_key] = (
                        _validator_classification_label(
                            found_targets,
                            required_targets,
                            logic,
                            'salmonella'
                        )
                    )
                else:
                    validator_generic_result_by_row[row_key] = (
                        _validator_classification_label(
                            found_targets,
                            required_targets,
                            logic,
                            'none'
                        )
                    )
                    validator_logic_by_row[row_key] = _validator_logic_label(
                        found_targets,
                        required_targets,
                        logic
                    )

    # Load primer sequences independently of sequence hits. This ensures that
    # perfect matches and primer/SEQID combinations without a SEQID model row
    # do not prevent other rows from receiving pseudoalignment output.
    primer_sequences = {}
    primer_set_query = primer_set_model.objects.filter(
        **{primer_request_filter: request.pk}
    ).prefetch_related("primer")

    for primer_set in primer_set_query:
        directional_sequences = {}
        unresolved_sequences = []

        for primer_object in primer_set.primer.all():
            direction = _primer_direction(primer_object.primer_header)
            if direction is None:
                unresolved_sequences.append(primer_object.primer_sequence)
            else:
                directional_sequences[direction] = primer_object.primer_sequence

        # Some uploaded primer headers do not end with -F/-R. For a standard
        # two-primer set, use model order as a deterministic fallback.
        if "forward" not in directional_sequences and unresolved_sequences:
            directional_sequences["forward"] = unresolved_sequences.pop(0)
        if "reverse" not in directional_sequences and unresolved_sequences:
            directional_sequences["reverse"] = unresolved_sequences.pop(0)

        primer_sequences[primer_set.primer_name] = directional_sequences

    def _model_value(model_object, attribute, default=""):
        """Read an attribute from an optional model object."""
        if model_object is None:
            return default
        if isinstance(model_object, dict):
            aliases = {
                "labid": "isolate_id",
                "serotype": "serovar",
            }
            return model_object.get(
                aliases.get(attribute, attribute),
                default,
            )
        return getattr(model_object, attribute, default)

    def _result(panel_name, seqid, primer_name, report_data):
        """Merge JSON report values with model alignment values."""
        data = report_data if isinstance(report_data, dict) else {}
        model_object = model_values.get((panel_name, seqid, primer_name))
        sequences = primer_sequences.get(primer_name, {})

        forward_mismatch = _get_field(
            data,
            ["forward_mismatch", "ForwardMismatches"],
            _model_value(model_object, "forward_mismatch"),
        )
        reverse_mismatch = _get_field(
            data,
            ["reverse_mismatch", "ReverseMismatches"],
            _model_value(model_object, "reverse_mismatch"),
        )
        forward_details = _get_field(
            data,
            ["forward_mismatch_details", "ForwardMismatchDetails"],
            _model_value(model_object, "forward_mismatch_details"),
        )
        reverse_details = _get_field(
            data,
            ["reverse_mismatch_details", "ReverseMismatchDetails"],
            _model_value(model_object, "reverse_mismatch_details"),
        )
        total_mismatch = _get_field(
            data,
            ["total_mismatch", "TotalMismatches"],
            _model_value(model_object, "total_mismatch"),
        )

        if total_mismatch in (None, ""):
            forward_value = _parse_mismatch(forward_mismatch)
            reverse_value = _parse_mismatch(reverse_mismatch)
            if forward_value is not None or reverse_value is not None:
                total_mismatch = (forward_value or 0) + (reverse_value or 0)

        forward_primer = sequences.get("forward", "")
        reverse_primer = sequences.get("reverse", "")
        forward_target = _model_value(model_object, "forward_ref")
        reverse_target = _model_value(model_object, "reverse_ref")

        # Prefer stored aligned query strings where available. Otherwise use
        # the original primer sequence from VerifierPrimers/ValidatorPrimers.
        forward_query = _model_value(model_object, "forward_query") or forward_primer
        reverse_query = _model_value(model_object, "reverse_query") or reverse_primer

        result = {
            "amplicon_length": _get_field(
                data,
                ["amplicon_length", "amplicon_size", "AmpliconSize"],
                _model_value(model_object, "amplicon_length"),
            ),
            "contig": _get_field(
                data,
                ["contig", "Contig"],
                _model_value(model_object, "contig"),
            ),
            "location": _location(data, model_object),
            "direction": _get_field(
                data,
                ["direction", "orientation"],
                _model_value(model_object, "direction"),
            ),
            "forward_mismatch": forward_mismatch,
            "forward_mismatch_details": _decode_details(forward_details) or "ND",
            "reverse_mismatch": reverse_mismatch,
            "reverse_mismatch_details": _decode_details(reverse_details) or "ND",
            "pseudoalignment": _combined_pseudoalignment(
                forward_query,
                forward_target,
                forward_details,
                reverse_query,
                reverse_target,
                reverse_details,
            ),
            "total_mismatch": total_mismatch,
        }
        result.update(_probe_values(data, primer_name))
        return result

    temp_file = tempfile.NamedTemporaryFile(
        suffix=".xlsx",
        delete=False,
    )
    temp_path = temp_file.name
    temp_file.close()

    try:
        workbook = xlsxwriter.Workbook(temp_path)
        header_options = {
            "bold": True,
            "bg_color": "#FFFFFF",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        }
        header_format = workbook.add_format(header_options)
        cell_format = workbook.add_format(
            {
                "border": 1,
                "valign": "vcenter",
            }
        )
        alignment_format = workbook.add_format(
            {
                "border": 1,
                "font_name": "Courier New",
                "font_size": 9,
                "valign": "vcenter",
                "text_wrap": True,
            }
        )
        gene_colors = (
            "#F2F2F2",  # gray
            "#DDEBF7",  # blue
            "#E2F0D9",  # green
            "#E4DFEC",  # purple
            "#E2E6C9",  # olive
            "#FCE4D6",  # orange
            "#FCE4EC",  # pink
            "#FFF2CC",  # yellow
            "#EAD7C0",  # brown
            "#D0E0E3",  # teal
            "#F4CCCC",  # coral
        )
        gene_formats = []
        gene_header_formats = []
        gene_alignment_formats = []
        for gene_color in gene_colors:
            gene_formats.append(workbook.add_format({
                "border": 1,
                "bg_color": gene_color,
                "valign": "vcenter",
                "text_wrap": True,
            }))
            gene_header_options = dict(header_options)
            gene_header_options["bg_color"] = gene_color
            gene_header_formats.append(workbook.add_format(gene_header_options))
            gene_alignment_formats.append(workbook.add_format({
                "border": 1,
                "bg_color": gene_color,
                "font_name": "Courier New",
                "font_size": 9,
                "valign": "vcenter",
                "text_wrap": True,
            }))

        worksheet = workbook.add_worksheet("PrimerVerifierResults")
        alignment_worksheet = workbook.add_worksheet("Pseudoalignments")
        alignment_headers = [
            "PANEL", "SEQID", "Primer", "Forward Mismatch",
            "Forward Mismatch Details", "Reverse Mismatch",
            "Reverse Mismatch Details", "Total Mismatches", "Pseudoalignment",
        ]
        for column, header in enumerate(alignment_headers):
            alignment_worksheet.write(0, column, header, header_format)
        alignment_row = 1
        headers = [
            "IsolateID", "SEQID", "Species", "Subspecies", "Serovar",
            "PANEL", "BENCHMARK PANEL", "RESULT",
        ]
        if validator_logic_applied:
            headers.append("LOGIC")
        headers.append("Benchmark Version")
        if include_gene_categories:
            headers.append("Matched Exclusion Categories")
        for primer_name in primer_names:
            for field_label, field_name in primer_fields:
                del field_name
                headers.append("{0}  {1}".format(field_label, primer_name))
            if has_probe:
                for field_label, field_name in probe_fields:
                    del field_name
                    headers.append(
                        "{0}  {1}".format(field_label, primer_name)
                    )
            if has_gene_references:
                headers.append("Gene Sequence Percent Match  {0}".format(
                    primer_name
                ))

        for column, header in enumerate(headers):
            metadata_columns = 9 + int(validator_logic_applied)
            if (
                column < metadata_columns
                or header in {
                    "LOGIC",
                    "Matched Exclusion Categories",
                }
            ):
                output_format = header_format
            else:
                gene_start = metadata_columns + int(include_gene_categories)
                gene_width = len(primer_fields)

                if has_probe:
                    gene_width += len(probe_fields)

                if has_gene_references:
                    gene_width += 1
                gene_index = (column - gene_start) // gene_width
                output_format = gene_header_formats[
                    gene_index % len(gene_header_formats)
                ]
            worksheet.write(0, column, header, output_format)

        panel_order = {"INCLUSIVITY": 0, "EXCLUSIVITY": 1}
        sorted_keys = sorted(
            entries,
            key=lambda item: (
                panel_order.get(item[0], 2),
                item[0],
                str(item[1] or ''),
            ),
        )

        for row, entry_key in enumerate(sorted_keys, start=1):
            panel_name, seqid = entry_key
            entry = entries[entry_key]
            metadata = metadata_by_panel_seqid.get(
                (panel_name.lower(), seqid),
                metadata_by_seqid.get(seqid),
            )
            isolate_id = _model_value(metadata, "labid")
            if isolate_id and not isinstance(isolate_id, str):
                isolate_id = getattr(isolate_id, "labid", str(isolate_id))
            isolate_id = isolate_id or seqid
            genus = str(_model_value(metadata, "genus", "")).strip().lower()
            worksheet.write(row, 0, isolate_id, cell_format)
            worksheet.write(row, 1, seqid, cell_format)
            worksheet.write(row, 2, _model_value(metadata, "species", "ND") or "ND", cell_format)
            worksheet.write(row, 3, _model_value(metadata, "subspecies", "ND") or "ND", cell_format)
            worksheet.write(row, 4, _model_value(metadata, "serotype", "ND") or "ND", cell_format)
            worksheet.write(row, 5, panel_name, cell_format)
            panel_benchmarks = (
                request.inclusivity_panel
                if panel_name == 'INCLUSIVITY'
                else request.exclusivity_panel
            )
            if not panel_benchmarks:
                benchmark_attribute = (
                    'inclusivity_benchmark'
                    if panel_name == 'INCLUSIVITY'
                    else 'exclusivity_benchmark'
                )
                scalar_benchmark = getattr(request, benchmark_attribute, '')
                panel_benchmarks = [scalar_benchmark] if scalar_benchmark else []
            benchmark_name = benchmark_panel_by_row.get((panel_name, seqid))
            if not benchmark_name and len(panel_benchmarks) == 1:
                benchmark_name = panel_benchmarks[0]
            benchmark_label = benchmark_labels.get(
                str(benchmark_name or '').strip().lower(),
                benchmark_name or 'ND',
            )
            worksheet.write(row, 6, benchmark_label, cell_format)
            benchmark_versions = request.benchmark_versions or {}
            benchmark_version = benchmark_versions.get(
                str(panel_benchmarks[0]).lower()
                if panel_benchmarks else '',
                ''
            )
            metadata_result = _model_value(metadata, "result", "")
            calculated_result = validator_classification_by_row.get(
                (panel_name, seqid)
            )
            if (
                isinstance(request, ValidatorRequest) and
                (panel_name, seqid) not in validator_generic_result_by_row and
                calculated_result is None
            ):
                # Legacy Validator requests without configured AND/OR logic
                # retain amplicon-only result behavior. Configured analyses
                # use the probe-aware found_targets calculation above.
                found_amplicon = any(
                    str(_get_field(
                        report_data,
                        ('amplicon_length', 'amplicon_size', 'AmpliconSize'),
                        ''
                    ) or '').strip().lower() not in (
                        '', 'nd', 'none', 'null', '0'
                    )
                    for report_data in entry['per_primer'].values()
                )
                validator_generic_result_by_row[(panel_name, seqid)] = (
                    'Detected' if found_amplicon else 'Not detected'
                )
            worksheet.write(
                row, 7,
                    calculated_result or (
                    validator_generic_result_by_row.get((panel_name, seqid))
                    if isinstance(request, ValidatorRequest)
                    else metadata_result or (
                            "Salmonella" if genus == "salmonella"
                            else "Not Salmonella"
                        )
                    ),
                cell_format,
            )
            column = 8
            if validator_logic_applied:
                worksheet.write(
                    row, column,
                    validator_logic_by_row.get((panel_name, seqid), ''),
                    cell_format,
                )
                column += 1
            worksheet.write(row, column, benchmark_version or "ND", cell_format)
            column += 1
            categories = []
            if isinstance(request, ValidatorRequest):
                category_results = request.gene_exclusion_results or {}
                normalized_seqid = _gene_query_key(seqid).lower()
                categories = category_results.get(seqid)
                if categories is None:
                    categories = next(
                        (
                            values for query, values in
                            category_results.items()
                            if _gene_query_key(query).lower() == normalized_seqid
                        ),
                        [],
                    )
            if include_gene_categories:
                worksheet.write(
                    row, column, ', '.join(categories) or 'ND', cell_format
                )
                column += 1
            for gene_index, primer_name in enumerate(primer_names):
                report_data = entry["per_primer"].get(primer_name, {})
                model_object = model_values.get((panel_name, seqid, primer_name))
                has_hit = bool(report_data) or model_object is not None
                result = _result(
                    panel_name,
                    seqid,
                    primer_name,
                    report_data,
                )

                mismatch_values = (
                    result.get('forward_mismatch'),
                    result.get('reverse_mismatch'),
                    result.get('total_mismatch'),
                )
                has_mismatch = False
                for mismatch_value in mismatch_values:
                    try:
                        if float(mismatch_value) > 0:
                            has_mismatch = True
                            break
                    except (TypeError, ValueError):
                        continue
                pseudoalignment = result.get('pseudoalignment')
                if has_mismatch and pseudoalignment not in (None, '', 'ND'):
                    alignment_values = [
                        panel_name,
                        seqid,
                        primer_name,
                        result.get('forward_mismatch') or 'ND',
                        result.get('forward_mismatch_details') or 'ND',
                        result.get('reverse_mismatch') or 'ND',
                        result.get('reverse_mismatch_details') or 'ND',
                        result.get('total_mismatch') or 'ND',
                        pseudoalignment,
                    ]
                    for alignment_column, alignment_value in enumerate(
                            alignment_values):
                        alignment_worksheet.write(
                            alignment_row,
                            alignment_column,
                            alignment_value,
                            alignment_format if alignment_column == 8
                            else cell_format,
                        )
                    alignment_worksheet.set_row(alignment_row, 105)
                    alignment_row += 1

                for field_label, field_name in primer_fields:
                    del field_label
                    value = result.get(field_name) if has_hit else "ND"
                    if value in (None, ""):
                        value = "ND"

                    output_format = gene_formats[
                        gene_index % len(gene_formats)
                    ]
                    worksheet.write(
                        row,
                        column,
                        value,
                        output_format,
                    )
                    column += 1

                if has_probe:
                    for field_label, field_name in probe_fields:
                        del field_label

                        value = result.get(field_name)
                        if value in (None, ""):
                            value = "ND"

                        worksheet.write(
                            row,
                            column,
                            value,
                            gene_formats[
                                gene_index % len(gene_formats)
                            ],
                        )
                        column += 1

                if has_gene_references:
                    percent_key = (
                        _gene_report_key(seqid).lower(),
                        primer_name.strip().lower(),
                    )
                    percent_match = gene_percent_matches.get(percent_key)
                    worksheet.write(
                        row, column,
                        percent_match if percent_match is not None else 'ND',
                        gene_formats[gene_index % len(gene_formats)],
                    )
                    column += 1

            worksheet.set_row(row, 20)

        worksheet.autofilter(
            0,
            0,
            max(len(sorted_keys), 1),
            max(len(headers) - 1, 0),
        )
        worksheet.freeze_panes(
            1, 9 + int(validator_logic_applied)
        )
        worksheet.set_row(0, 54)
        worksheet.set_column(0, 0, 22)
        worksheet.set_column(1, 1, 14)
        worksheet.set_column(2, 4, 22)
        worksheet.set_column(5, 6, 22)
        if validator_logic_applied:
            worksheet.set_column(7, 7, 22)
            worksheet.set_column(8, 8, 18)
        else:
            worksheet.set_column(7, 7, 18)
        if include_gene_categories:
            category_column = 8 + int(validator_logic_applied)
            worksheet.set_column(category_column, category_column, 28)
        column = 8 + int(validator_logic_applied) + int(
            include_gene_categories
        )
        for primer_name in primer_names:
            for field_label, field_name in primer_fields:
                del field_label
                if field_name == "pseudoalignment":
                    worksheet.set_column(column, column, 48)
                elif field_name == "contig":
                    worksheet.set_column(column, column, 22)
                elif field_name == "location":
                    worksheet.set_column(column, column, 18)
                else:
                    worksheet.set_column(column, column, 15)
                column += 1
            if has_probe:
                for field_label, field_name in probe_fields:
                    del field_label

                    if field_name == "probe_mismatch_details":
                        width = 30
                    elif field_name == "probe_evaluation_method":
                        width = 22
                    elif field_name == "probe_evaluation_threshold":
                        width = 20
                    elif field_name == "probe_percent_identity":
                        width = 18
                    elif field_name == "probe_total_differences":
                        width = 18
                    else:
                        width = 15

                    worksheet.set_column(
                        column,
                        column,
                        width
                    )
                    column += 1
            if has_gene_references:
                worksheet.set_column(column, column, 18)
                column += 1

        audit_worksheet = workbook.add_worksheet("GeneCategoryAudit")
        audit_headers = [
            "Report", "Gene", "Category", "Reason", "Query", "Identity",
            "Coverage", "Orientation", "Subject Start", "Subject End",
            "Subject Length", "Query Start", "Query End", "Bit Score",
            "Reference Gap", "Stop Subject Location", "Codon Change",
            "Paired Query", "Paired Identity",
            "Paired Subject Start", "Paired Subject End",
        ]
        for column, header in enumerate(audit_headers):
            audit_worksheet.write(0, column, header, header_format)

        audit_row = 1
        for report_name, target_details in (
                getattr(request, 'gene_exclusion_details', {}) or {}).items():
            for target, evidence_items in target_details.items():
                for evidence in evidence_items:
                    hit = evidence.get('hit') or evidence.get('first_hit') or {}
                    paired_hit = evidence.get('second_hit') or {}
                    audit_values = [
                        report_name, target, evidence.get('category', ''),
                        evidence.get('reason', ''), hit.get('query', ''),
                        hit.get('identity', ''), hit.get('coverage', ''),
                        hit.get('orientation', ''), hit.get('subject_start', ''),
                        hit.get('subject_end', ''), hit.get('subject_length', ''),
                        hit.get('query_start', ''), hit.get('query_end', ''),
                        hit.get('bit_score', ''), evidence.get('gap', ''),
                        ', '.join(
                            stop.get('subject_location', '')
                            for stop in evidence.get('stop_evidence', [])
                        ),
                        ', '.join(
                            stop.get('codon_change', '')
                            for stop in evidence.get('stop_evidence', [])
                            if stop.get('codon_change')
                        ),
                        paired_hit.get('query', ''), paired_hit.get('identity', ''),
                        paired_hit.get('subject_start', ''),
                        paired_hit.get('subject_end', ''),
                    ]
                    for column, value in enumerate(audit_values):
                        audit_worksheet.write(audit_row, column, value, cell_format)
                    audit_row += 1
        audit_worksheet.autofilter(
            0, 0, max(audit_row - 1, 1), len(audit_headers) - 1
        )
        audit_worksheet.freeze_panes(1, 0)
        audit_worksheet.set_row(0, 36)
        audit_worksheet.set_column(0, 1, 24)
        audit_worksheet.set_column(2, 2, 20)
        audit_worksheet.set_column(3, 3, 60)
        audit_worksheet.set_column(4, 18, 18)

        alignment_worksheet.autofilter(
            0, 0, max(alignment_row - 1, 1), len(alignment_headers) - 1
        )
        alignment_worksheet.freeze_panes(1, 0)
        alignment_worksheet.set_row(0, 36)
        alignment_worksheet.set_column(0, 0, 16)
        alignment_worksheet.set_column(1, 1, 20)
        alignment_worksheet.set_column(2, 2, 24)
        alignment_worksheet.set_column(3, 7, 20)
        alignment_worksheet.set_column(8, 8, 72)

        workbook.close()

        blob_name = "reports/{0}_summary_report.xlsx".format(container_name)
        blob_client.create_blob_from_path(
            container_name=container_name,
            blob_name=blob_name,
            file_path=temp_path,
            content_settings=ContentSettings(
                content_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            ),
        )
    finally:
        try:
            os.remove(temp_path)
        except OSError:
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

    # Azure may return virtual-folder marker blobs such as "reports/gene"
    # alongside the files below them. They must not be downloaded as files.
    report_names = [blob.name.rstrip('/') for blob in report_blobs]
    report_blobs = [
        blob for blob in report_blobs
        if blob.name.rstrip('/') and not blob.name.endswith('/') and not any(
            other.startswith(blob.name.rstrip('/') + '/')
            for other in report_names
        )
    ]

    if not report_blobs:
        # Nothing to archive
        return "{0}_reports.zip".format(container_name)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Local base "reports" dir to mirror the virtual folder
        local_reports_dir = os.path.join(tmpdir, "reports")
        os.makedirs(local_reports_dir, exist_ok=True)

        # Download each blob to the temp dir
        for b in report_blobs:
            local_path = os.path.join(tmpdir, b.name)  # includes 'reports/...' in path
            local_dir = os.path.dirname(local_path)
            if not os.path.isdir(local_dir):
                os.makedirs(local_dir, exist_ok=True)
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
