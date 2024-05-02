#!/usr/bin/env python

# Django-related imports
from django.conf import settings  # To access azure credentials

# Standard imports
import smtplib
import json
import os

# Third-party imports
from azure.storage.blob import BlockBlobService
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Portal-specific imports
from olc_webportalv2.primer_finder.models import PrimerVerifierRequest, \
    VerifierPrimerSet, \
    VerifierPrimers, \
    VerifierPanel, \
    VerifierSEQID, \
    ValidatorRequest, \
    ValidatorPanel, \
    ValidatorPrimerSet, \
    ValidatorPrimers
from olc_webportalv2.cowbat.methods import AzureBatch


def upload_primers(request):
    """
    Create a local folder to store the batch config file, and upload the primer sequence to blob storage
    :param request:
    """
    # Set the container name appropriately
    container_name = request.container_namer()
    run_folder = os.path.join('olc_webportalv2', 'media', '{container_name}'.format(container_name=container_name))
    # Create the run folder as necessary
    os.makedirs(run_folder, exist_ok=True)
    # The primer sequences are stored in a generic file name
    file_name = 'primers.fasta'
    # Create a blob service client to manipulate blobs
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY)
    # Set the name of the container in which the primer file is to be stored
    input_container = container_name + '-input'
    # Create the container
    blob_client.create_container(input_container)
    # Create a string of the primer sequences. Only PrimerVerifierRequests have the .primer_sequences attribute
    try:
        primer_sequences = request.primer_sequences
    except AttributeError:
        primer_sequences = '{forward_primer}\r\n{reverse_primer}\r\n'\
            .format(forward_primer=request.forward_primer,
                    reverse_primer=request.reverse_primer)
    # Write the bytes-encoded primer sequences to blob storage
    blob_client.create_blob_from_bytes(
        container_name=input_container,
        blob_name=file_name,
        blob=primer_sequences.encode('utf-8'))
    return container_name, run_folder, file_name, input_container


def upload_probe(request):
    # The probe sequence is stored in a generic file name
    file_name = 'probe.fasta'
    # Create a blob service client to manipulate blobs
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Set the container name appropriately
    container_name = request.container_namer()
    # Set the name of the container in which the probe file is to be stored
    input_container = container_name + '-input'
    # Add a header to the probe sequence
    probe_sequence = '>probe\r\n{sequence}'.format(sequence=request.probe_sequence)
    # Write the bytes-encoded primer sequences to blob storage
    blob_client.create_blob_from_bytes(
        container_name=input_container,
        blob_name=file_name,
        blob=probe_sequence.encode('utf-8')
    )


def format_batch_config(job_name, vm_size):
    batch_string = str()
    batch_string += 'BATCH_ACCOUNT_NAME:={}\n'.format(settings.BATCH_ACCOUNT_NAME)
    batch_string += 'BATCH_ACCOUNT_KEY:={}\n'.format(settings.BATCH_ACCOUNT_KEY)
    batch_string += 'BATCH_ACCOUNT_URL:={}\n'.format(settings.BATCH_ACCOUNT_URL)
    batch_string += 'STORAGE_ACCOUNT_NAME:={}\n'.format(settings.AZURE_ACCOUNT_NAME)
    batch_string += 'STORAGE_ACCOUNT_KEY:={}\n'.format(settings.AZURE_ACCOUNT_KEY)
    batch_string += 'JOB_NAME:={}\n'.format(job_name)
    batch_string += 'VM_IMAGE:={}\n'.format(settings.VM_IMAGE)
    batch_string += 'VM_CLIENT_ID:={}\n'.format(settings.VM_CLIENT_ID)
    batch_string += 'VM_SECRET:={}\n'.format(settings.VM_SECRET)
    batch_string += 'VM_SIZE:={}\n'.format(vm_size)
    batch_string += 'VM_TENANT:={}\n'.format(settings.VM_TENANT)
    return batch_string


def send_email(subject, body, recipient):
    fromaddr = 'cfia.foodport.donotreply-nepasrepondre.aliport.acia@inspection.gc.ca'
    toaddr = recipient
    msg = MIMEMultipart()
    msg['From'] = fromaddr
    msg['To'] = toaddr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    server = smtplib.SMTP('email-smtp.ca-central-1.amazonaws.com', 587)
    server.starttls()
    server.login(user=os.environ.get('EMAIL_HOST_USER'), password=os.environ.get('EMAIL_HOST_PASSWORD'))
    text = msg.as_string()
    server.sendmail(fromaddr, toaddr, text)


def populate_primer_sets(pk, primer_list, model=VerifierPrimerSet):
    """
    Populate the database using the PrimerSet model
    :param pk: type int: Primary key of the PrimerVerifierRequest
    :param primer_list: type list: List of all primer base names (e.g. LinA-F + LinA-R -> LinA)
    :param model: type models.Model: PrimerSet model to populate. Default is VerifierPrimerSet
    """
    id_string = str()
    if model == VerifierPrimerSet:
        id_string = 'verifier_request_id'
    elif model == ValidatorPrimerSet:
        id_string = 'validator_request_id'
    else:
        pass

    for primer in primer_list:
        # Use keyword argument unpacking to allow for the use of a generic method
        keyword_dict = {
            id_string: pk,
            'primer_name': primer
        }
        # Check to see if the database already contains PrimerSet objects matching the primary key of the
        # model and the primer name
        if not model.objects.filter(**keyword_dict):
            # Create the entry in the database
            primer_set = model(**keyword_dict)
            # Save the entry
            primer_set.save()


def populate_primer_details(query, details, model=VerifierPrimers):
    """
    Populate the database using the Primers model
    :param query: type query_set: PrimerSet.objects.filter(verifier_request_id=PrimerVerifierRequest_primary_key).
    The query returns a list of the primer base names (e.g. ['LinA', 'LinB'])
    :param details: type dict: Dictionary of primer base name: primer name: sequence
    e.g. LinA: { 'LinA-F': 'GAACGA...', 'LinA-R': 'TTTGAT...' }, LinB: { 'LinB-F'.... }
    :param model: type models.Model: PrimerSet model to populate. Default is VerifierPrimers
    """
    # Iterate through all the primer base names
    for primer_set in query:
        # Extract the forward and reverse primer dictionary from the details dictionary using the primer base name
        primers = details[primer_set.primer_name]
        # Iterate through the primer name and sequence extracted from the primer dictionary
        for primer_name, primer_sequence in primers.items():
            # Use keyword argument unpacking to allow for the use of a generic method
            keyword_dict = {
                'primer_id': primer_set.pk,
                'primer_header': primer_name
            }
            # Check to see if the database already contains Primers objects matching the primary key of the PrimerSet
            # and the detailed primer name
            if not model.objects.filter(**keyword_dict):
                # Create the entry in the database
                primer_object = model(**keyword_dict)
                # Save the entry
                primer_object.save()


def populate_panel(genera, pk, panel_type, panel=VerifierPanel):
    """
    Populate the database using the supplied panel
    :param genera: type query_set: PrimerVerifierRequest.objects.filter(pk=primary_key)[0].panel_details
    The query returns a list containing the genus/genera in the appropriate panel (e.g. ['escherichia', 'listeria'])
    :param pk: type int: Primary key of the PrimerVerifierRequest
    :param panel_type: type str: String of the panel type. Options are 'inclusivity', and 'exclusivity'
    :param panel: type model.Model. Default is VerifierPanel
    """
    # Iterate through all the genera within the desired panel
    for genus in genera:
        # The genera in the model are lowercase, so they must be manipulated to the desired formatting e.g. Escherichia
        genus = genus.capitalize()
        # Select the correct panel
        id_string = str()
        if panel == VerifierPanel:
            id_string = 'verifier_request_id'
        elif panel == ValidatorPanel:
            id_string = 'validator_request_id'
        else:
            pass
        # Use keyword argument unpacking to allow for the use of a generic method
        keyword_dict = {
            id_string: pk,
            'genus': genus,
            'panel': panel_type
        }
        # Check to see if the model already contains objects matching the primary key of the model and the genus
        if not panel.objects.filter(**keyword_dict):
            # Create the entry in the database
            panel_object = panel(**keyword_dict)
            # Save the entry
            panel_object.save()


def retrieve_panel_seqids(seq_dictionary, seq_path_dict, container_name, genus):
    """
    Find all the SEQIDs associated with each genus-specific validation panel
    :return: seq_dictionary: Dictionary of genus: list of SEQIDs
    :return: seq_path_dict: Dictionary of genus: seqid: sequence file path
    """
    # Create a client to access the Blob storage account
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Extract all the blobs in the container
    blobs = blob_client.list_blobs(container_name=container_name)
    # Iterate through all the blobs
    for blob in blobs:
        # The container contains folders with assemblies 2013-SEQ-0034.fasta. Strip off the .fasta extension
        seqid = os.path.splitext(blob.name)[0]
        # Initialise the genus key in the dictionaries as required
        if genus not in seq_dictionary:
            seq_dictionary[genus] = list()
            seq_path_dict[genus] = dict()
        # Append the SEQID to the list
        seq_dictionary[genus].append(seqid)
        # Append the file path and name to the list
        seq_path_dict[genus][seqid] = blob.name

    return seq_dictionary, seq_path_dict


def populate_sequences(seqs, seq_path_dict, panel_query, primer_query, seqid_model):
    """
    Populate the database using the supplied panel (SEQID) and sequences.
    :param seqs: type dict: Dictionary of genus: list of SEQIDs
    :param seq_path_dict: type dict: Dictionary of genus: seqid: sequence file path
    :param panel_query: type query_set: Panel.objects.filter(verifier_request_id=primary_key)
    :param primer_query: type query_set: PrimerSet.objects.filter(verifier_request_id=primary_key)
    :param seqid_model: type django.db.models.Model: VerifierSEQID
    The query returns Panel objects corresponding to the primary key of the PrimerVerifierRequest
    Options are 'inclusivity_genus', and 'exclusivity_genus'
    """
    for primer in primer_query:
        # Iterate through the panel objects in the query set
        for panel in panel_query:
            # Extract the genus from the query.
            genus = panel.genus
            # Extract the list of SEQIDs from the dictionary using the genus as the key
            seqids = seqs[genus]
            # Iterate through the SEQIDs
            for seqid in seqids:
                # Check to see if the database already contains SEQID objects matching the primary key of the
                # Panel and the SEQID
                if not seqid_model.objects.filter(panel_id=panel.pk, seqid=seqid, primer_id=primer.pk):
                    # Create the entry in the database. Initialise values that will be populated later
                    sequences = seqid_model(
                        panel_id=panel.pk,
                        primer_id=primer.pk,
                        seqid=seqid,
                        sequence_path=seq_path_dict[genus][seqid]
                    )
                    # Save the entry
                    sequences.save()


def download_container(container_name, output_dir):
    """
    Download the contents of a container in Azure storage to local storage
    :param container_name: type str: Name of container to download
    :param output_dir: type str: Path into which the contents of the container are to be saved
    """
    # Create a blob service client to allow manipulation of Azure resources
    blob_service = BlockBlobService(
        account_key=settings.AZURE_ACCOUNT_KEY,
        account_name=settings.AZURE_ACCOUNT_NAME
    )
    # Download the desired container to the specified path
    AzureBatch.download_container(
        blob_service=blob_service,
        container_name=container_name,
        output_dir=output_dir
    )


def populate_report(request):
    """
    Download the JSON-formatted report from blob storage, parse it, and store the contents in the
    PrimerVerifierRequest model
    :param request: type django.db.models.Model: PrimerVerifierRequest filtered by primary key of current
    analyses
    :return verifier_request: Updated verifier_request
    """
    # Set the container name appropriately
    container_name = request.container_namer()
    run_folder = os.path.join('olc_webportalv2', 'media', '{container_name}'.format(container_name=container_name))
    report_folder = os.path.join(run_folder, 'reports')
    # Check to see if the report_folder is already present locally
    if not os.path.isdir(report_folder) and not request.report:
        # Download the output container (contains stdout and stderr text files from the batch VM, as well as the
        # reports folder)
        download_container(
            container_name=container_name + '-output',
            output_dir=run_folder
        )
    # Set the name of the .json report file
    json_report_file = os.path.join(report_folder, 'inclusivity_exclusivity_report.json')
    # Open the file
    report_object = open(json_report_file, 'r')
    # Use the json library to read in the file
    json_report = json.load(report_object)
    # Close the file
    report_object.close()
    # Populate the model with the json.dumps formatted report. Add some formatting, so it is easier to read
    request.report = json.dumps(json_report, sort_keys=True, indent=4, separators=(',', ': '))
    # Save the model
    request.save()
    return request


def retrieve_sets(request, primer_set_model, panel_model, analysis):
    """
    Extract the primer and panel query sets from models using the primary key of a request
    :param request: type django.db.models.Model: PrimerVerifierRequest filtered by primary key of current
    :param primer_set_model: type django.db.models.Model: VerifierPrimerSet
    :param panel_model: django.db.models.Model: VerifierPanel
    :param analysis: type str: String of the current analysis type. Default is verifier
    :return: report: JSON-formatted PrimerValidator report
    :return: primer_set: Query set of objects from primer_set_model corresponding to request.pk
    :return: panel_set: Query set of objects from panel_model corresponding to request.pk
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


def populate_sequence_details(request, primer_set_model, panel_model, seqid_model, analysis='verifier'):
    """
    Parse the JSON-formatted report, and populate the SEQID model with sequence-specific information analyses
    :param request: type django.db.models.Model: PrimerVerifierRequest filtered by primary key of current
    :param primer_set_model: type django.db.models.Model: VerifierPrimerSet
    :param panel_model: django.db.models.Model: VerifierPanel
    :param seqid_model: type django.db.models.Model: VerifierSEQID
    :param analysis: type str: String of the current analysis type. Default is verifier
    :return request: Updated request
    """
    # Create the necessary query sets for populating the SEQID model
    report, primer_set, panel_set = retrieve_sets(
        request=request,
        primer_set_model=primer_set_model,
        panel_model=panel_model,
        analysis=analysis
    )
    # The JSON report is organised as follows: primer base name: panel type: SEQID: attribute name: attribute value
    # To populate the model, use the appropriate keys from the queries above
    # Iterate through the primer objects
    for primer in primer_set:
        # Iterate through the panel objects
        for panel in panel_set:
            # Retrieve the SEQID object(s) corresponding to the current Panel object
            sequence_set = seqid_model.objects.filter(panel=panel)
            # Iterate through the sequence objects
            for sequence in sequence_set:
                # If primers do not create an amplicon with that sequence, an empty dictionary is returned
                # e.g. '2014-SEQ-0121': {}
                if not report[primer.primer_name][panel.panel][sequence.seqid]:
                    continue
                # Since the SEQID model has seqids corresponding to particular primer + panel combinations, ensure that
                # all the names are right
                if primer.primer_name != sequence.primer.primer_name or panel.panel != sequence.panel.panel or \
                        panel.genus != sequence.panel.genus:
                    continue
                # Extract the sequence-specific dictionary from the report
                sequence_data = report[primer.primer_name][panel.panel][sequence.seqid]
                # Populate the model with the corresponding values from the dictionary
                sequence.primer = primer
                sequence.amplicon_length = sequence_data['amplicon_length']
                sequence.contig = sequence_data['contig']
                sequence.direction = sequence_data['direction']
                sequence.forward_mismatch = sequence_data['forward_mismatch']
                sequence.forward_mismatch_details = sequence_data['forward_mismatch_details']
                sequence.forward_pos = sequence_data['forward_pos']
                sequence.forward_query = sequence_data['forward_query']
                sequence.forward_ref = sequence_data['forward_ref']
                sequence.primer_set = sequence_data['primer_set']
                sequence.reverse_mismatch = sequence_data['reverse_mismatch']
                sequence.reverse_mismatch_details = sequence_data['reverse_mismatch_details']
                sequence.reverse_pos = sequence_data['reverse_pos']
                sequence.reverse_query = sequence_data['reverse_query']
                sequence.reverse_ref = sequence_data['reverse_ref']
                sequence.sequence = sequence_data['sequence']
                sequence.start_pos = sequence_data['start_pos'][0]
                sequence.stop_pos = sequence_data['stop_pos'][0]
                sequence.total_mismatch = sequence_data['total_mismatch']
                # Save the model
                sequence.save()
    return request


def create_details(request, primer_set_model, panel_model, seqid_model, analysis='verifier'):
    """
    Parse the verifier_report.report to extract panel- and genus-specific details
    :param request: type django.db.models.Model: PrimerVerifierRequest filtered by primary key of current
    analyses
    :param primer_set_model: type django.db.models.Model: VerifierPrimerSet
    :param panel_model: django.db.models.Model: VerifierPanel
    :param seqid_model: type django.db.models.Model: VerifierSEQID
    :param analysis: type str: String of the current analysis type. Default is verifier
    :return request: Updated request
    """
    # Create the necessary query sets for populating the SEQID model
    report, primer_set, panel_set = retrieve_sets(
        request=request,
        primer_set_model=primer_set_model,
        panel_model=panel_model,
        analysis=analysis
    )
    details = dict()
    totals = dict()
    for primer in primer_set:
        # Create a variable to avoid writing primer.primer_name every time
        primer_name = primer.primer_name
        if primer_name not in details:
            details[primer_name] = dict()
            totals[primer_name] = dict()
        # Iterate through the panel objects
        for panel in panel_set:
            # Create variables to save on typing
            panel_name = panel.panel
            genus = panel.genus
            # Initialise the necessary keys
            if panel_name not in details[primer_name]:
                details[primer_name][panel_name] = {
                    genus: dict(),
                    'percent': '{:.1f}'.format(report[primer_name][panel_name]['percent']),
                    'positive': report[primer_name][panel_name]['positive'],
                    'total': report[primer_name][panel_name]['total'],
                }
                totals[primer_name][panel_name] = dict()
            if genus not in details[primer_name][panel_name]:
                details[primer_name][panel_name][genus] = dict()
            if 'combined' not in details[primer_name][panel_name]:
                details[primer_name][panel_name]['combined'] = dict()
            # Retrieve the SEQID object(s) corresponding to the current Panel object
            sequence_set = seqid_model.objects.filter(panel=panel)
            # Initialise variables to store the total number of sequences in a panel, as well as the number of
            # sequences with hits with the current primer
            panel_total = 0
            panel_positive = 0
            # Iterate through all the sequence objects
            for sequence in sequence_set:
                # Ensure that the current sequence corresponds to the current primer (e.g. Lin), panel
                # (e.g. inclusivity), and genus (e.g. Escherichia)
                if primer_name != sequence.primer.primer_name or panel_name != sequence.panel.panel or \
                        genus != sequence.panel.genus:
                    continue
                # Increment the total number of panel-specific sequences
                panel_total += 1
                # If primers do not create an amplicon with that sequence, an empty dictionary is returned
                # e.g. '2014-SEQ-0121': {}
                if not sequence.amplicon_length:
                    continue
                # Since the sequence has an amplicon_length attribute, increment the number of panel-specific
                # positive sequences
                panel_positive += 1
                #
                totals = calculate_totals(
                    sequence=sequence,
                    primer_name=primer_name,
                    totals=totals,
                    panel_name=panel_name,
                    genus=genus
                )
                # Populate the dictionary if the sequence has the forward_mismatch_details attribute
                if sequence.forward_mismatch_details:
                    # Initialise the forward_mismatches and forward_details keys as necessary for both the current
                    # genus and the combined panel
                    if 'forward_mismatches' not in details[primer_name][panel_name][genus]:
                        details[primer_name][panel_name][genus][
                            'forward_mismatches'] = dict()
                        details[primer_name][panel_name][genus][
                            'forward_details'] = dict()
                    if 'forward_mismatches' not in details[primer_name][panel_name]['combined']:
                        details[primer_name][panel_name]['combined'][
                            'forward_mismatches'] = dict()
                        details[primer_name][panel_name]['combined'][
                            'forward_details'] = dict()
                    # Add a counter to the forward_details and forward_mismatches keys in the dictionary
                    if sequence.forward_mismatch_details not in \
                            details[primer_name][panel_name][genus]['forward_details']:
                        details[primer_name][panel_name][genus]['forward_details'][
                            sequence.forward_mismatch_details] = 0
                        details[primer_name][panel_name][genus]['forward_mismatches'][
                            sequence.forward_mismatch] = 0
                    if sequence.forward_mismatch_details not in \
                            details[primer_name][panel_name]['combined']['forward_details']:
                        details[primer_name][panel_name]['combined']['forward_details'][
                            sequence.forward_mismatch_details] = 0
                        details[primer_name][panel_name]['combined']['forward_mismatches'][
                            sequence.forward_mismatch] = 0
                    # Increment the values for the keys
                    details[primer_name][panel_name][genus]['forward_details'][
                        sequence.forward_mismatch_details] += 1
                    details[primer_name][panel_name][genus]['forward_mismatches'][
                        sequence.forward_mismatch] += 1
                    details[primer_name][panel_name]['combined']['forward_details'][
                        sequence.forward_mismatch_details] += 1
                    details[primer_name][panel_name]['combined']['forward_mismatches'][
                        sequence.forward_mismatch] += 1
                # Same as above, but with the reverse read
                if sequence.reverse_mismatch_details:
                    if 'reverse_mismatches' not in details[primer_name][panel_name][genus]:
                        details[primer_name][panel_name][genus][
                            'reverse_mismatches'] = dict()
                        details[primer_name][panel_name][genus][
                            'reverse_details'] = dict()
                    if 'reverse_mismatches' not in details[primer_name][panel_name]['combined']:
                        details[primer_name][panel_name]['combined'][
                            'reverse_mismatches'] = dict()
                        details[primer_name][panel_name]['combined']['reverse_details'] = dict()
                    if sequence.reverse_mismatch_details not in \
                            details[primer_name][panel_name][genus]['reverse_details']:
                        details[primer_name][panel_name][genus]['reverse_details'][
                            sequence.reverse_mismatch_details] = 0
                        details[primer_name][panel_name][genus]['reverse_mismatches'][
                            sequence.reverse_mismatch] = 0
                    if sequence.reverse_mismatch_details not in \
                            details[primer_name][panel_name]['combined']['reverse_details']:
                        details[primer_name][panel_name]['combined']['reverse_details'][
                            sequence.reverse_mismatch_details] = 0
                        details[primer_name][panel_name]['combined']['reverse_mismatches'][
                            sequence.reverse_mismatch] = 0
                    details[primer_name][panel_name][genus]['reverse_details'][
                        sequence.reverse_mismatch_details] += 1
                    details[primer_name][panel_name][genus]['reverse_mismatches'][
                        sequence.reverse_mismatch] += 1
                    details[primer_name][panel_name]['combined']['reverse_details'][
                        sequence.reverse_mismatch_details] += 1
                    details[primer_name][panel_name]['combined']['reverse_mismatches'][
                        sequence.reverse_mismatch] += 1
            # Sort the dictionaries with "item of interest: number of hits" by the number of hits
            # Forward mismatches (genus-specific and combined)
            try:
                details[primer_name][panel_name]['combined']['forward_mismatches'] = \
                    sorted(
                        details[primer_name][panel_name]['combined']['forward_mismatches'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
            except KeyError:
                pass
            try:
                details[primer_name][panel_name][genus]['forward_mismatches'] = \
                    sorted(
                        details[primer_name][panel_name][genus]['forward_mismatches'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
            except KeyError:
                pass
            # Forward details
            try:
                details[primer_name][panel_name]['combined']['forward_details'] = \
                    sorted(
                        details[primer_name][panel_name]['combined']['forward_details'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
            except KeyError:
                pass
            try:
                details[primer_name][panel_name][genus]['forward_details'] = \
                    sorted(
                        details[primer_name][panel_name][genus]['forward_details'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
            except KeyError:
                pass
            # Reverse mismatches
            try:
                details[primer_name][panel_name]['combined']['reverse_mismatches'] = \
                    sorted(
                        details[primer_name][panel_name]['combined']['reverse_mismatches'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
            except KeyError:
                pass
            try:
                details[primer_name][panel_name][genus]['reverse_mismatches'] = \
                    sorted(
                        details[primer_name][panel_name][genus]['reverse_mismatches'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
            except KeyError:
                pass
            # Reverse details
            try:
                details[primer_name][panel_name]['combined']['reverse_details'] = \
                    sorted(
                        details[primer_name][panel_name]['combined']['reverse_details'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
            except KeyError:
                pass
            try:
                details[primer_name][panel_name][genus]['reverse_details'] = \
                    sorted(
                        details[primer_name][panel_name][genus]['reverse_details'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )
            except KeyError:
                pass
            # Initialise the current genus in the dictionary as required
            if genus not in details[primer_name][panel_name]:
                details[primer_name][panel_name][genus] = dict()
            # Calculate the percentage of sequences with amplicons
            try:
                details[primer_name][panel_name][genus]['percent'] = float(
                    '{:.2f}'.format(panel_positive / panel_total * 100))
            # If there are no sequences in a panel, set the percentage to 0
            except ZeroDivisionError:
                details[primer_name][panel_name][genus]['percent'] = 0.00
            details[primer_name][panel_name][genus]['positive'] = panel_positive
            details[primer_name][panel_name][genus]['total'] = panel_total
    # Update the request with the dictionary
    request.summary = json.dumps(details, sort_keys=True, indent=4, separators=(',', ': '))
    # print(json.dumps(details, sort_keys=True, indent=4, separators=(',', ': ')))
    request.totals = json.dumps(totals, sort_keys=True, indent=4, separators=(',', ': '))
    request.save()
    # Create a client to manipulate blobs in the storage account
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Create a blob of the summary report from the stored details
    blob_client.create_blob_from_bytes(
        container_name=request.container_namer() + '-output',
        blob_name=os.path.join('reports', '{name}_summary_report.json').format(name=request.container_namer()),
        # Encode the string to bytes, and use it to create the blob
        blob=request.summary.encode('utf-8')
    )
    return request


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
    print(totals)
    # if sequence.forward_mismatch_details and sequence.reverse_mismatch_details:
    # print(sequence.seqid, sequence.forward_mismatch_details, sequence.reverse_mismatch_details,
    #           sequence.total_mismatch)
    return totals


def exclusivity_panel_retrieve(inclusivity):
    """
    Extract corresponding exclusivity panel for a given inclusivity panel
    :param inclusivity: type query_set: ValidatorRequest.objects.filter(pk=primary_key)[0].panel_details
    The query returns a list containing the genus in the appropriate panel (e.g. ['escherichia'])
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
    # The genera in the model are lowercase, so they must be manipulated to the desired formatting e.g. Escherichia
    inclusivity = inclusivity.capitalize()
    # Extract the exclusivity panel from the dictionary
    exclusivity_panel = inclusivity_exclusivity_dict[inclusivity]
    return exclusivity_panel
