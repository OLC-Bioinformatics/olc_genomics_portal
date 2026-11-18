#!/usr/bin/env python

"""
Methods for the primer_finder app
"""

# Standard imports
import csv
import json
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, Tuple, Type, Union

# Third-party imports
import xlsxwriter

# Django-related imports
from django.db.models.query import QuerySet

from olc_webportalv2.common.methods import send_email as send_email
from olc_webportalv2.common.storage_client import get_storage_client

# Portal-specific imports
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorPanel,
    ValidatorPrimers,
    ValidatorPrimerSet,
    ValidatorRequest,
    ValidatorSEQID,
    ValidatorTarget,
    VerifierPanel,
    VerifierPrimers,
    VerifierPrimerSet,
    VerifierSEQID,
)

logger = logging.getLogger(__name__)


def upload_primers(
    *,  # Enforce keyword arguments
    request: Any,
):
    """
    Create a local folder to store the batch config file, and upload the
    primer sequence to blob storage

    Args:
        request: The request object containing primer information.
    """
    # Set the container name appropriately
    container_name = request.container_namer()
    run_folder = os.path.join('olc_webportalv2', 'media', f'{container_name}')

    # Create the run folder as necessary
    os.makedirs(run_folder, exist_ok=True)

    # The primer sequences are stored in a generic file name
    file_name = 'primers.fasta'

    # Create a blob service client to manipulate blobs
    blob_client = get_storage_client()

    # Create the container
    blob_client.ensure_container(container_name)

    # Create a string of the primer sequences. Only PrimerVerifierRequests
    # have the .primer_sequences attribute
    try:
        primer_sequences = request.primer_sequences
    except AttributeError:
        primer_sequences = (
            f'{request.forward_primer}\r\n{request.reverse_primer}\r\n'
        )

    # Write the bytes-encoded primer sequences to blob storage
    blob_client.upload_bytes(
        container_name,
        file_name,
        primer_sequences.encode('utf-8'),
    )

    return container_name, file_name


def upload_probe(request: ValidatorRequest):
    """
    Upload the probe sequence to blob storage

    Args:
        request: Validator request containing the probe sequence and
            storage-container name.
    """
    # The probe sequence is stored with a generic file name
    file_name = 'probe.fasta'

    # Create a blob service client to manipulate blobs
    blob_client = get_storage_client()

    # Set the container name appropriately
    container_name = request.container_namer()

    # Add a header to the probe sequence
    probe_records = _validator_probe_records(request)
    probe_sequence = (
        '\r\n'.join(
            f'>{name}\r\n{sequence}' for name, sequence in probe_records
        )
        + '\r\n'
    )

    # Write the bytes-encoded primer sequences to blob storage
    blob_client.ensure_container(container_name)
    blob_client.upload_bytes(
        container_name,
        file_name,
        probe_sequence.encode('utf-8'),
    )


def _validator_probe_records(request):
    """Return all configured target probes, or the legacy single probe."""
    if isinstance(request, ValidatorRequest):
        target_probes = [
            (target.name, target.probe_sequence.strip())
            for target in ValidatorTarget.objects.filter(
                sample__validator_request=request
            ).exclude(probe_sequence='')
        ]
        if target_probes:
            return target_probes
    sequence = (getattr(request, 'probe_sequence', '') or '').strip()
    return [('probe', sequence)] if sequence else []


def populate_primer_sets(
    *,  # Ensure that the following arguments are keyword arguments
    pk: int,
    primer_list: list,
    model: Type[
        Union[VerifierPrimerSet, ValidatorPrimerSet]
    ] = VerifierPrimerSet,
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
    id_string = (
        'verifier_request_id'
        if model == VerifierPrimerSet
        else 'validator_request_id'
    )

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
    model: Type[Union[VerifierPrimers, ValidatorPrimers]] = VerifierPrimers,
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
        model.objects.filter(primer_id__in=query).values_list(
            'primer_id', 'primer_header'
        )
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
                    model(
                        primer_id=primer_set.pk,
                        primer_header=primer_name,
                        primer_sequence=primer_sequence,
                    )
                )

    # Use bulk_create to efficiently add all new primer objects
    model.objects.bulk_create(new_primer_objects)


def populate_panel(
    *,  # Ensure that the following arguments are keyword arguments
    genera: list,
    panel_type: str,
    pk: int,
    panel: Type[Union[VerifierPanel, ValidatorPanel]] = VerifierPanel,
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
    id_string = (
        'verifier_request_id'
        if panel == VerifierPanel
        else 'validator_request_id'
    )

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
    seqids_file = os.path.join(run_folder, f'{genus}_seqids.txt')

    if not os.path.isfile(seqids_file):
        filenames = os.listdir(run_folder) if os.path.isdir(run_folder) else []
        versioned_files = sorted(
            os.path.join(run_folder, filename)
            for filename in filenames
            if filename.startswith(f'{genus}-')
            and filename.endswith('_seqids.txt')
        )
        if len(versioned_files) == 1:
            seqids_file = versioned_files[0]
        elif versioned_files:
            raise ValueError(
                f'Multiple versioned sequence ID files found for {genus}: '
                f'{versioned_files}'
            )
        else:
            logger.warning(
                'Sequence ID file for %s was not found in %s',
                genus,
                run_folder,
            )
            return seq_dictionary

    # Read the SEQIDs from the file
    with open(seqids_file, 'r', encoding='utf-8') as f:
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


def populate_report(
    *,  # Ensure that the following arguments are keyword arguments
    request: Type[Union[PrimerVerifierRequest, ValidatorRequest]],
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
            'olc_webportalv2', 'media', f'{container_name}'
        )
        report_folder = os.path.join(run_folder, 'reports')

        if not os.path.isdir(report_folder) and not request.report:
            _download_reports(
                container_name=container_name, run_folder=run_folder
            )

        if not request.report:
            request = _load_and_store_report(
                report_folder=report_folder, request=request
            )

        return request, report_folder

    except Exception:
        logger.exception(
            'Error while populating report for %s',
            request.container_namer(),
        )
        raise


def _download_reports(
    *,  # Ensure that the following arguments are keyword arguments
    container_name: str,
    run_folder: str,
):
    """
    Download the reports from Azure Blob Storage.

    Args:
        container_name (str): The name of the container.
        run_folder (str): The path to the run folder.
    """
    # Create a blob service client to manipulate blobs
    blob_service = get_storage_client()

    _download_storage_objects(
        blob_service=blob_service,
        container_name=container_name,
        blobs=blob_service.list_objects(container_name),
        run_folder=run_folder,
    )


def _download_storage_objects(
    *,
    blob_service,
    container_name: str,
    blobs,
    run_folder: str,
):
    """Download storage objects while recreating virtual directories."""
    blobs = list(blobs)
    directory_names = set()
    for blob in blobs:
        parent, separator, _ = blob.name.rstrip('/').rpartition('/')
        while separator:
            directory_names.add(parent)
            parent, separator, _ = parent.rpartition('/')

    for blob in blobs:
        blob_name = blob.name.rstrip('/')
        is_directory = blob.name.endswith('/') or (
            getattr(blob, 'size', None) == 0
            and blob_name in directory_names
        )
        if is_directory:
            directory_path = os.path.join(run_folder, blob_name)
            if (
                os.path.isfile(directory_path)
                and os.path.getsize(directory_path) == 0
            ):
                os.remove(directory_path)
            os.makedirs(directory_path, exist_ok=True)
            continue

        blob_service.download_file(
            container_name,
            blob.name,
            os.path.join(run_folder, blob.name),
        )


def _load_and_store_report(
    *,  # Ensure that the following arguments are keyword arguments
    report_folder: str,
    request: PrimerVerifierRequest,
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
        report_folder, 'inclusivity_exclusivity_report.json'
    )

    # Load the JSON report
    with open(json_report_file, 'r', encoding='utf-8') as report_object:
        # Use json.load to load the JSON report
        json_report = json.load(report_object)

        # Store the JSON report in the request object
        request.report = json.dumps(
            json_report, sort_keys=True, indent=4, separators=(',', ': ')
        )
        request.save()
    return request


def retrieve_sets(
    request: Union[PrimerVerifierRequest, ValidatorRequest],
    primer_set_model: Union[VerifierPrimerSet, ValidatorPrimerSet],
    panel_model: Union[VerifierPanel, ValidatorPanel],
    analysis: str,
):
    """
    Extract the primer and panel query sets from models using the primary key
    of a request

    :param request: type django.db.models.Model: PrimerVerifierRequest or
    ValidatorRequest filtered by primary key of current request
    :param primer_set_model: type django.db.models.Model: VerifierPrimerSet or
    ValidatorPrimerSet
    :param panel_model: VerifierPanel or ValidatorPanel model class.
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
    keyword_dict = {id_string: request.pk}
    # Retrieve primer-set objects for the request.
    primer_set = primer_set_model.objects.filter(**keyword_dict)
    # Retrieve the Panel object(s) corresponding to the request primary key
    panel_set = panel_model.objects.filter(**keyword_dict)
    return report, primer_set, panel_set


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
    totals: Dict,
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


def sort_mismatch_details(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict,
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
                if (
                    genus != 'percent'
                    and genus != 'positive'
                    and genus != 'total'
                )
            ] + [location_data.get('combined', {})]:
                for mismatch_type in ['forward', 'reverse']:
                    mismatches_key = f'{mismatch_type}_mismatches'
                    details_key = f'{mismatch_type}_details'
                    if isinstance(location, dict):
                        if mismatches_key in location:
                            try:
                                location[mismatches_key] = sorted(
                                    location[mismatches_key].items(),
                                    key=lambda item: (-item[1], item[0]),
                                )
                            except AttributeError:
                                pass
                        if details_key in location:
                            try:
                                location[details_key] = sorted(
                                    location[details_key].items(),
                                    key=lambda item: (-item[1], item[0]),
                                )
                            except AttributeError:
                                pass


def create_blob_from_summary(
    *,  # Ensure that the following arguments are keyword arguments
    request: PrimerVerifierRequest,
) -> None:
    """
    Creates a blob from the summary report in Azure Blob Storage.

    :param request: PrimerVerifierRequest object
    """
    blob_client = get_storage_client()
    container_name = request.container_namer()
    blob_client.upload_bytes(
        container_name,
        os.path.join(
            'reports', f'{request.container_namer()}_summary_report.json'
        ),
        request.summary.encode('utf-8'),
    )


def create_details(
    *,  # Ensure that the following arguments are keyword arguments
    panel_model: Type[Union[VerifierPanel, ValidatorPanel]],
    primer_set_model: Type[Union[VerifierPrimerSet, ValidatorPrimerSet]],
    request: Type[Union[PrimerVerifierRequest, ValidatorRequest]],
    analysis: str = 'verifier',
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
        analysis=analysis,
    )

    details, totals = initialize_data_structures()

    # Load the report data from request.report
    report = json.loads(request.report)
    probe_configuration = {}
    if isinstance(request, ValidatorRequest):
        for configured_target in ValidatorTarget.objects.filter(
            sample__validator_request=request
        ):
            probe_configuration[configured_target.name.strip().lower()] = (
                configured_target
            )

    # Create a dictionary to store seq_dict for each genus
    seq_dict_by_genus = {}

    # Populate seq_dict for each genus
    run_folder = os.path.join(
        'olc_webportalv2', 'media', f'{request.container_namer()}'
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
            details=details, primer_name=primer_name, totals=totals
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
                sequence_data = (
                    report.get(primer_name, {})
                    .get(panel_name, {})
                    .get(seqid, {})
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
                    totals=totals,
                )

                configured_target = probe_configuration.get(
                    primer_name.strip().lower()
                )
                probe_accepted = not (
                    configured_target and configured_target.probe_sequence
                )

                # Probe prevalence and target-specific detection threshold.
                probe_info = _probe_info_for_target(
                    sequence_data=sequence_data,
                    target_name=primer_name,
                )
                if isinstance(probe_info, dict):
                    probe_total += 1
                    method = getattr(
                        configured_target,
                        'probe_evaluation_method',
                        'percent_identity',
                    )
                    percent_threshold = float(
                        getattr(
                            configured_target,
                            'probe_percent_identity',
                            80.0 if configured_target else 90.0,
                        )
                    )
                    difference_threshold = int(
                        getattr(configured_target, 'probe_max_differences', 2)
                    )
                    try:
                        observed_identity = float(
                            probe_info.get(
                                'percent_id',
                                probe_info.get('percent_identity'),
                            )
                        )
                    except (TypeError, ValueError):
                        observed_identity = None
                    try:
                        differences = int(
                            float(
                                probe_info.get(
                                    'mismatches',
                                    probe_info.get('mismatch', 0),
                                )
                            )
                        ) + int(
                            float(
                                probe_info.get(
                                    'gaps', probe_info.get('gap', 0)
                                )
                            )
                        )
                    except (TypeError, ValueError):
                        differences = None
                    if method == 'max_differences':
                        probe_hit = (
                            differences is not None
                            and differences <= difference_threshold
                        )
                    else:
                        probe_hit = (
                            observed_identity is not None
                            and observed_identity >= percent_threshold
                        )
                    if probe_hit:
                        probe_positive += 1
                        probe_accepted = True

                if hit and probe_accepted:
                    panel_positive += 1
                    total_dict.update(totals)

            # Calculate panel percentages *after* the loop
            panel_percentages = calculate_panel_percentage_from_counts(
                panel_total=panel_total, panel_positive=panel_positive
            )

            # Update the details dictionary with the calculated percentages
            details[primer_name][panel_name][genus]['percent'] = (
                panel_percentages['percent']
            )
            details[primer_name][panel_name][genus]['positive'] = (
                panel_percentages['positive']
            )
            details[primer_name][panel_name][genus]['total'] = (
                panel_percentages['total']
            )

            # Store probe prevalence stats if any probe data was found
            if probe_total > 0:
                try:
                    probe_percent = float(
                        f'{probe_positive / panel_total * 100:.2f}'
                    )
                except ZeroDivisionError:
                    probe_percent = 0.00
                genus_details = details[primer_name][panel_name][genus]
                genus_details['probe_prevalence'] = {
                    'percent': probe_percent,
                    'positive': probe_positive,
                    'negative': panel_total - probe_positive,
                    'total': panel_total,
                }

    sort_mismatch_details(details=details)
    if isinstance(request, ValidatorRequest):
        details['_metadata'] = {
            'benchmark_versions': request.benchmark_versions or {},
        }

    request.summary = json.dumps(
        details, sort_keys=True, indent=4, separators=(',', ': ')
    )
    request.totals = json.dumps(
        totals, sort_keys=True, indent=4, separators=(',', ': ')
    )
    request.save()

    # Create a blob from the summary report
    create_blob_from_summary(request=request)

    return request


def _probe_info_for_target(sequence_data, target_name):
    """Return probe metrics for a target, including legacy report shapes."""
    probe_data = (
        sequence_data.get('probe')
        if isinstance(sequence_data, dict)
        else None
    )
    if not isinstance(probe_data, dict):
        return None

    target_key = str(target_name or '').strip().lower()
    probe_info = next(
        (
            value
            for key, value in probe_data.items()
            if str(key).strip().lower() == target_key
            and isinstance(value, dict)
        ),
        None,
    )
    if probe_info is not None:
        return probe_info

    legacy_probe = probe_data.get('probe')
    if isinstance(legacy_probe, dict):
        return legacy_probe
    if any(isinstance(value, dict) for value in probe_data.values()):
        return None
    return probe_data


def validator_sample_results(request):
    """Evaluate target logic and classification for each reported sequence."""
    if not isinstance(request, ValidatorRequest) or not request.report:
        return []
    try:
        report = json.loads(request.report)
    except (TypeError, ValueError):
        return []

    results = []
    samples = request.samples.prefetch_related('targets')
    panels = request.panel_details.all()
    for sample in samples:
        targets = list(sample.targets.all())
        target_names = {
            target.name.strip().lower(): target for target in targets
        }
        required_targets = set(target_names)
        if sample.classification_rule == 'salmonella':
            required_targets.intersection_update(('inva', 'stn'))
        for panel in panels:
            panel_name = panel.panel
            seqids = set()
            for target in targets:
                primer_report = report.get(target.name, {})
                panel_report = primer_report.get(panel_name, {})
                seqids.update(
                    key
                    for key in panel_report
                    if key not in ('percent', 'positive', 'total')
                )
            for seqid in sorted(seqids):
                found_targets = set()
                for target_name, target in target_names.items():
                    sequence_data = (
                        report.get(target.name, {})
                        .get(panel_name, {})
                        .get(seqid, {})
                    )
                    if not isinstance(sequence_data, dict) or not (
                        sequence_data.get('amplicon_length')
                    ):
                        continue
                    if target.probe_sequence:
                        probe = _probe_info_for_target(
                            sequence_data=sequence_data,
                            target_name=target.name,
                        )
                        if not isinstance(probe, dict):
                            continue
                        try:
                            if (
                                target.probe_evaluation_method
                                == 'max_differences'
                            ):
                                differences = int(
                                    float(
                                        probe.get(
                                            'mismatches',
                                            probe.get('mismatch', 0),
                                        )
                                    )
                                ) + int(
                                    float(
                                        probe.get('gaps', probe.get('gap', 0))
                                    )
                                )
                                if differences > target.probe_max_differences:
                                    continue
                            else:
                                identity = float(
                                    probe.get(
                                        'percent_id',
                                        probe.get('percent_identity'),
                                    )
                                )
                                if identity < target.probe_percent_identity:
                                    continue
                        except (TypeError, ValueError):
                            continue
                    found_targets.add(target_name)

                if sample.classification_rule == 'salmonella':
                    classification = (
                        'Salmonella'
                        if found_targets.intersection(('inva', 'stn'))
                        else 'Not Salmonella'
                    )
                elif sample.logic == 'AND':
                    classification = (
                        'Detected'
                        if found_targets == required_targets
                        else 'Not detected'
                    )
                elif sample.logic == 'OR':
                    classification = (
                        'Detected' if found_targets else 'Not detected'
                    )
                else:
                    classification = ''

                results.append(
                    {
                        'sample': sample.name,
                        'panel': panel_name,
                        'seqid': seqid,
                        'found_targets': sorted(found_targets),
                        'classification': classification,
                    }
                )
    return results


def find_shared_seqids(
    *,  # Ensure that the following arguments are keyword arguments
    seq_dictionary: Dict,
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

    logger.debug('Shared sequence IDs: %s', shared_seqids)
    return shared_seqids


def process_sequence_data_from_report(
    *,  # Ensure that the following arguments are keyword arguments
    details: Dict,
    sequence_data: Dict,
    primer_name: str,
    panel_name: str,
    genus: str,
    totals: Dict,
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
        logger.exception(
            'Sequence report data does not contain amplicon_length'
        )
        raise
    if not amplicon_length:
        return totals, details, False

    totals = calculate_totals_from_report(
        sequence_data=sequence_data,
        primer_name=primer_name,
        totals=totals,
        panel_name=panel_name,
        genus=genus,
    )

    details = update_mismatch_details_from_report(
        sequence_data=sequence_data,
        details=details,
        primer_name=primer_name,
        panel_name=panel_name,
        genus=genus,
    )
    # details = update_mismatch_details_from_report(
    #     sequence_data=sequence_data,
    #     details=details,
    #     primer_name=primer_name,
    #     panel_name=panel_name,
    #     genus=genus,
    #     mismatch_type='reverse'
    # )

    logger.debug('Primer report totals: %s', totals)
    return totals, details, True


def calculate_totals_from_report(
    *,  # Enforce keyword arguments
    sequence_data: Dict[str, Any],
    totals: Dict[str, Any],
    primer_name: str,
    panel_name: str,
    genus: str,
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
    genus_mismatch_totals = genus_totals.setdefault('total_mismatch', {})
    combined_mismatch_totals = combined_totals.setdefault(
        'total_mismatch',
        {},
    )

    # Initialize mismatch counts dictionaries
    genus_mismatch_counts = genus_mismatch_totals.setdefault(
        total_mismatch, {}
    )
    combined_mismatch_counts = combined_mismatch_totals.setdefault(
        total_mismatch, {}
    )

    # Increment the total counts
    genus_mismatch_counts['totals'] = (
        genus_mismatch_counts.get('totals', 0) + 1
    )
    combined_mismatch_counts['totals'] = (
        combined_mismatch_counts.get('totals', 0) + 1
    )

    # Get forward and reverse mismatch details
    forward_mismatch_details = sequence_data.get(
        'forward_mismatch_details', '0'
    )
    reverse_mismatch_details = sequence_data.get(
        'reverse_mismatch_details', '0'
    )

    # Combine mismatch details
    total_details = ', '.join(
        (
            forward_mismatch_details or '0',
            reverse_mismatch_details or '0',
        )
    )

    # Increment the mismatch details counts
    genus_mismatch_counts[total_details] = (
        genus_mismatch_counts.get(total_details, 0) + 1
    )
    combined_mismatch_counts[total_details] = (
        combined_mismatch_counts.get(total_details, 0) + 1
    )

    return totals


def update_mismatch_details_from_report(
    *,  # Enforce keyword arguments
    details: Dict[str, Any],
    genus: str,
    panel_name: str,
    primer_name: str,
    sequence_data: Dict[str, Any],
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
            f'{mismatch_type}_mismatch_details'
        )
        mismatch = sequence_data.get(f'{mismatch_type}_mismatch')

        # If no mismatch details, continue
        if not mismatch_details:
            continue

        for location in [genus, 'combined']:
            # Get panel data, initializing if necessary
            if location not in details[primer_name][panel_name]:
                details[primer_name][panel_name][location] = {}

            # Initialize mismatch details dictionary if necessary
            mismatch_details_key = f'{mismatch_type}_details'
            if (
                mismatch_details_key
                not in details[primer_name][panel_name][location]
            ):
                details[primer_name][panel_name][location][
                    mismatch_details_key
                ] = {}

            # Initialize mismatch counts dictionary if necessary
            mismatch_counts_key = f'{mismatch_type}_mismatches'
            if (
                mismatch_counts_key
                not in details[primer_name][panel_name][location]
            ):
                details[primer_name][panel_name][location][
                    mismatch_counts_key
                ] = {}

            # Increment the mismatch details count
            if (
                mismatch_details
                not in details[primer_name][panel_name][location][
                    mismatch_details_key
                ]
            ):
                details[primer_name][panel_name][location][
                    mismatch_details_key
                ][mismatch_details] = 0
            details[primer_name][panel_name][location][mismatch_details_key][
                mismatch_details
            ] += 1

            # Increment the mismatch count
            if mismatch:
                if (
                    mismatch
                    not in details[primer_name][panel_name][location][
                        mismatch_counts_key
                    ]
                ):
                    details[primer_name][panel_name][location][
                        mismatch_counts_key
                    ][mismatch] = 0
                details[primer_name][panel_name][location][
                    mismatch_counts_key
                ][mismatch] += 1
    return details


def calculate_panel_percentage_from_counts(
    *,  # Ensure that the following arguments are keyword arguments
    panel_total: int,
    panel_positive: int,
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
        percent = float(f'{panel_positive / panel_total * 100:.2f}')
    except ZeroDivisionError:
        percent = 0.00

    return {
        'percent': percent,
        'positive': panel_positive,
        'total': panel_total,
    }


def exclusivity_panel_retrieve(inclusivity):
    """
    Extract corresponding exclusivity panel for a given inclusivity panel
    :param inclusivity: type query_set:
    ValidatorRequest.objects.filter(pk=primary_key)[0].panel_details
    The query returns a list containing the genus in the appropriate panel
    (e.g. ['escherichia'])
    """


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

    from olc_webportalv2.primer_finder.validator_gene_reports import (
        _gene_percent_match,
        _gene_query_key,
        _gene_report_key,
        _gene_value,
    )

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
        probe_info = _probe_info_for_target(data, primer_name)
        if probe_info is None:
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
    if isinstance(request, PrimerVerifierRequest):
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
            selected_benchmarks = [
                str(name or '').strip().lower()
                for name in panel_benchmarks
                if str(name or '').strip()
            ]
            if not benchmark_name:
                if (
                    'bds-listeria' in selected_benchmarks
                    and str(seqid).upper().startswith('BDS-LM')
                ):
                    benchmark_name = 'bds-listeria'
                else:
                    other_benchmarks = [
                        name for name in selected_benchmarks
                        if name != 'bds-listeria'
                    ]
                    if len(other_benchmarks) == 1:
                        benchmark_name = other_benchmarks[0]
                    elif len(selected_benchmarks) == 1:
                        benchmark_name = selected_benchmarks[0]
            benchmark_label = benchmark_labels.get(
                str(benchmark_name or '').strip().lower(),
                benchmark_name or 'ND',
            )
            worksheet.write(row, 6, benchmark_label, cell_format)
            benchmark_versions = request.benchmark_versions or {}
            benchmark_version = benchmark_versions.get(
                str(benchmark_name or '').strip().lower(),
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
        blob_client.upload_file(
            container_name,
            blob_name,
            temp_path,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def _archive_reports_and_upload(
    *,  # Force keyword args
    blob_client,
    container_name: str,
):
    """
    Download all blobs under reports/, zip them, and upload the archive to the
    container root as <container_name>_reports.zip. Returns the blob name.
    """

    # Collect the list of report blobs (prefix 'reports/')
    report_blobs = [
        b for b in blob_client.list_objects(container_name, prefix='reports/')
    ]

    if not report_blobs:
        # Nothing to archive
        return f'{container_name}_reports.zip'

    with tempfile.TemporaryDirectory() as tmpdir:
        # Local base "reports" dir to mirror the virtual folder
        local_reports_dir = os.path.join(tmpdir, 'reports')
        os.makedirs(local_reports_dir)

        # Download each blob to the temp dir
        _download_storage_objects(
            blob_service=blob_client,
            container_name=container_name,
            blobs=report_blobs,
            run_folder=tmpdir,
        )

        # Create zip archive that includes the "reports" folder at top-level
        base_name = os.path.join(tmpdir, f'{container_name}_reports')
        archive_path = shutil.make_archive(
            base_name=base_name,
            format='zip',
            root_dir=tmpdir,
            base_dir='reports',
        )

        # Upload to the container root
        zip_blob_name = f'{container_name}_reports.zip'
        blob_client.upload_file(
            container_name,
            zip_blob_name,
            archive_path,
            content_type='application/zip',
        )
        return zip_blob_name
