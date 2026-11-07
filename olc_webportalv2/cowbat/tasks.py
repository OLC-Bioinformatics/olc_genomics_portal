"""
Collection of tasks for the COWBAT app
"""

# Standard library imports
import csv
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import fnmatch
from glob import glob
from io import StringIO
import json
import logging
import os
import re
import shutil
import smtplib
from time import sleep
import zipfile


# Third-party library imports
import azure.batch.batch_service_client as batch
import azure.batch.batch_auth as batch_auth
import azure.batch.models as batchmodels
from azure.batch.models import (
    BatchErrorException,
    TaskState
)
from azure.storage.blob import (
    BlockBlobService,
    BlobPermissions,
)
from azure.common import AzureMissingResourceHttpError
from Bio import SeqIO
from celery import shared_task
import ete3
import pandas as pd
import requests
from sentry_sdk import capture_exception
from strainchoosr import strainchoosr

# Django-related imports
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from olc_webportalv2.ampliseq.models import AmpliSeqRequest
from olc_webportalv2.ampliseq.tasks import check_ampliseq_tasks
from olc_webportalv2.common.benchmarks import (
    normalise_benchmark_name,
)
from olc_webportalv2.cowbat.models import (
    SequencingRun,
    AzureTask,
    SummaryMetadata,
)
from olc_webportalv2.common.research_storage import get_research_storage
from olc_webportalv2.cowsnphr.models import COWSNPhRRequest
from olc_webportalv2.cowsnphr.tasks import (
    check_cowsnphr_tasks,
    delete_pool_job,
)
from olc_webportalv2.filezone.models import Regexes
from olc_webportalv2.geneseekr.models import (
    TreeAzureRequest,
    Tree,
    AMRSummary,
    AMRAzureRequest,
    AMRDetail,
    ProkkaRequest,
    ProkkaAzureRequest,
    GeneSeekrRequest,
    GeneSeekrDetail,
    GeneSeekrAzureRequest,
    NearestNeighbors,
    TopBlastHit,
)
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorRequest,
)
from olc_webportalv2.primer_finder.tasks import (
    check_verifier_tasks,
    check_validator_tasks,
)
from olc_webportalv2.vir_typer.models import (
    VirTyperAzureRequest,
    VirTyperProject,
)

import hashlib
from django.db import transaction
from django.db.models import Q
from django.utils import timezone as django_timezone
from olc_webportalv2.cowbat.models import NanoporeFile, NanoporeManifest, NanoporeProcessingBatch


@shared_task
def rename_research_container_task(sequencing_run_pk, old_name, new_name):
    """Move a research run's blobs and update its database record."""
    sequencing_run = SequencingRun.objects.get(pk=sequencing_run_pk)
    sequencing_run.rename_status = 'running'
    sequencing_run.rename_target = new_name
    sequencing_run.save(update_fields=['rename_status', 'rename_target'])

    try:
        get_research_storage().rename_container(old_name, new_name)
    except Exception as exc:
        sequencing_run.rename_status = 'failed'
        sequencing_run.errors = (sequencing_run.errors or []) + [
            'Rename failed: {0}'.format(str(exc)[:220])
        ]
        sequencing_run.save(update_fields=['rename_status', 'rename_target', 'errors'])
        raise

    sequencing_run.run_name = new_name
    sequencing_run.container = new_name
    sequencing_run.rename_status = 'complete'
    sequencing_run.rename_target = new_name
    sequencing_run.save(update_fields=[
        'run_name',
        'container',
        'rename_status',
        'rename_target',
    ])
    return new_name


@shared_task
def run_cowbat_batch(
        sequencing_run_pk,
        vm_size='Standard_D32s_v3',
        container=None):
    """
    Run the cowbat batch task.

    Parameters:
    sequencing_run_pk (int): Primary key of the sequencing run.
    vm_size (str): Virtual machine size. Default is 'Standard_D32s_v3'.
    container (str): Container name. Default is None.

    Returns:
    None
    """
    # Get the sequencing run object
    sequencing_run = SequencingRun.objects.get(pk=sequencing_run_pk)
    run_folder = os.path.join(
        'olc_webportalv2',
        'media',
        str(sequencing_run)
    )

    # Set the name of the container
    sequencing_run.container = str(sequencing_run).lower().replace('_', '-')
    sequencing_run.save()

    try:
        # Create a blob client
        blob_client = BlockBlobService(
            account_key=settings.AZURE_ACCOUNT_KEY,
            account_name=settings.AZURE_ACCOUNT_NAME)

        # Check if all files are present. If not, change status to
        # 'UploadError'
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        container_name = sequencing_run.run_name.lower().replace('_', '-')
        blob_filenames = list()
        blobs = blob_client.list_blobs(container_name=container_name)
        for blob in blobs:
            blob_filenames.append(blob.name)
        all_files_present = True
        for seqid in sequencing_run.seqids:
            forward_reads = fnmatch.filter(blob_filenames, seqid + '*_R1*')
            reverse_reads = fnmatch.filter(blob_filenames, seqid + '*_R2*')
            if len(forward_reads) != 1 or len(reverse_reads) != 1:
                all_files_present = False

        if all_files_present is False:
            sequencing_run.status = 'UploadError'
            sequencing_run.save()
            return

        # Process NextSeq runs differently
        if sequencing_run.nextseq:
            print('Processing NextSeq')
            nextseq_run(
                blob_client=blob_client,
                blob_files=blob_filenames,
                container_name=container_name,
                sequencing_run=sequencing_run
            )
            return

        # Submit the batch API request
        submit_batch(
            run_folder=run_folder,
            sequencing_run=sequencing_run
        )
    except Exception as e:
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        fh = logging.FileHandler(os.path.join(run_folder, 'error'))
        fh.setLevel(logging.INFO)
        logger.addHandler(fh)
        logger.exception(e)
        capture_exception(e)
        SequencingRun.objects.filter(
            pk=sequencing_run_pk
        ).update(status='Error')


def submit_batch(
        run_folder: str,
        sequencing_run: SequencingRun,
        vm_size: str = 'Standard_D32s_v3'
        ) -> None:
    """
    Submits a batch job to Azure Batch service and creates an AzureTask object
    to track the job.

    This function creates an Azure Batch job with the specified settings and
    then creates a new AzureTask object to track the job. The Azure Batch job
    runs on a virtual machine of the specified size, and the input container
    is kept after the job is done. The exit code of the job is stored in a
    file in the run folder.

    Parameters:
    run_folder (str): The path of the folder where the run files are located.
    sequencing_run (SequencingRun): The sequencing run object associated with
    the job.
    vm_size (str, optional): The size of the virtual machine to use for the
    job. Defaults to 'Standard_D32s_v3'.

    Returns:
    None
    """

    # Create a variable to store the extremely long path information
    path = '$AZ_BATCH_NODE_MOUNTS_DIR/{container}'.format(
        container=sequencing_run.container
    )

    # Define the system call
    command = (
        'source $CONDA/activate /envs/cowbat && '
        'mkdir -p /datadrive/{run_name} && '
        'cp -R {path} /datadrive/ && '
        'assembly_pipeline.py '
        '-s /datadrive/{run_name} '
        '-r /databases/0.5.0.23'.format(
            path=path,
            run_name=sequencing_run.container
        )
    )

    # Add basic assembly and preprocess arguments as required
    if sequencing_run.basic_assembly:
        command += ' -b'
    if sequencing_run.preprocess:
        command += ' -p'

    # Update the command with the final steps to copy the files back from the
    # datadrive to the container
    command += (
        ' ; cp -R /datadrive/{run_name} $AZ_BATCH_NODE_MOUNTS_DIR'.format(
            run_name=sequencing_run.container
        )
    )

    # Print the command
    print(command)

    # Create the API call
    cowbat_api_submit(
        command=command,
        run_folder=run_folder,
        sequencing_run=sequencing_run,
        vm_size=vm_size
    )


def cowbat_api_submit(
        command: str,
        run_folder: str,
        sequencing_run: SequencingRun,
        vm_size: str,
        ) -> None:
    """
    Submits a job to the Batch API.

    This function sends a POST request to the Batch API with the specified
    command, container name, and VM size.  It then updates the provided
    SequencingRun object with the response data from the API and saves
    the changes. Finally, it creates a new AzureTask object associated with
    the SequencingRun.

    Args:
        command (str): The command to be run.
        run_folder (str): The folder where the run files are located.
        sequencing_run (SequencingRun): The SequencingRun object to be updated.
        vm_size (str): The size of the VM where the command will be run.

    Returns:
        None
    """
    # Define the data
    data = {
        "container": sequencing_run.container,
        "command_file": command,
        "vm_size": vm_size,
        "input_file_pattern": None,
        "download_file_pattern": None,
        "analysis_type": "COWBAT",
        "unique_id": 'FoodPort'
    }

    print(data)

    # Make the POST request with a timeout of 10 seconds
    response = requests.post(
        settings.BATCH_SERVICE_URL,
        headers=settings.BATCH_URL_HEADERS,
        data=json.dumps(data),
        timeout=10
    )

    # Get the JSON data from the response
    response_data = response.json()

    # Print the data
    print('Batch API response', response_data)

    # Update the model with the response data
    sequencing_run.pool_id = response_data['pool_id']
    sequencing_run.job_id = response_data['job_id']
    # This is only taking the first entry from the tasks, as there should
    # only be one
    sequencing_run.task_id = response_data['tasks'][0]
    sequencing_run.batch_submit_status = response_data['status']
    sequencing_run.batch_submit_errors = response_data['error']

    # Save the changes
    sequencing_run.save()

    # Create a new AzureTask object
    AzureTask.objects.create(
        sequencing_run=sequencing_run,
        exit_code_file=os.path.join(run_folder, 'exit_codes.txt')
    )


def nextseq_run(
        blob_client: BlockBlobService,
        blob_files: list,
        container_name: str,
        sequencing_run: SequencingRun,
        ):
    """
    Split NextSeq runs into manageable sizes, copy blobs to appropriate
    containers and run the assembly pipeline on each sub-sequencing run
    :param blob_client: BlockBlobService object
    :param blob_files: List of blob files
    :param sequencing_run: SequencingRun object
    """
    # Check if a sample sheet is present
    if 'SampleSheet.csv' in blob_files:
        print('Sample sheet present')
        sub_runs = sample_sheet(
            blob_client=blob_client,
            container_name=container_name,
            run_name=sequencing_run.run_name,
            file_names=blob_files
        )
    else:
        print('No sample sheet')
        # Create the sub_runs dictionary without a sample sheet
        sub_runs = no_sample_sheet(
            blob_client=blob_client,
            blob_files=blob_files,
            container_name=container_name,
        )
    # Submit the each sub-sequencing job to Azure Batch
    for i in sub_runs:
        run_name = sequencing_run.run_name
        container_name = run_name + '-{i}'.format(i=i)
        print('Processing run {run_name} in container {container}'.format(
            run_name=run_name,
            container=container_name
        ))

        # Set the path to store the configuration file
        local_path = os.path.join(os.path.join(
            'olc_webportalv2',
            'media',
            run_name,
            'sub_sample_sheets-{i}'.format(i=i)
            )
        )
        print('Local run folder: {local_path}'.format(local_path=local_path))

        # Create a SequenceRun object for each sub-sequencing run
        sub_sequencing_run = SequencingRun.objects.get_or_create(
            run_name=container_name,
            container=container_name,
            seqids=sub_runs[i],
            basic_assembly=sequencing_run.basic_assembly,
            preprocess=sequencing_run.preprocess,
            nextseq=True,
            progress='Processing',
        )
        # If the SequencingRun object already exists, a tuple is returned.
        # Extract the SequencingRun object
        # from the tuple
        if isinstance(sub_sequencing_run, tuple):
            sub_sequencing_run = sub_sequencing_run[0]
        print(
            'Processing {sequencing_run} with pk {pk} and type {type_run}'
            .format(
                sequencing_run=str(sub_sequencing_run),
                pk=sub_sequencing_run.pk,
                type_run=type(sub_sequencing_run)
            )
        )
        try:
            #  Recreate the archive name
            # archive_name = 'sub_sample_sheets-{i}.zip'.format(i=i)

            # Redefine the batch nodes path
            path = '$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}'.format(
                container_name=container_name
            )

            # Define the system call
            command = (
                'source $CONDA/activate /envs/cowbat && '
                'mkdir -p /datadrive/{run_name} && '
                'cp -R {path} /datadrive/ && '
                # 'unzip /datadrive/{run_name}/{zip_file} '
                # '-d /datadrive/{run_name} && '
                'assembly_pipeline.py '
                '-s /datadrive/{run_name} '
                '-r /databases/0.5.0.23 && '
                'cp -R /datadrive/{run_name} $AZ_BATCH_NODE_MOUNTS_DIR'.format(
                    path=path,
                    run_name=container_name,
                    # zip_file=archive_name
                )
            )

            # Submit the API batch request
            cowbat_api_submit(
                command=command,
                run_folder=os.path.join(local_path, 'exit_codes.txt'),
                sequencing_run=sub_sequencing_run,
                vm_size='Standard_D48s_v3'
            )
        except Exception as exc:
            logger = logging.getLogger()
            logger.setLevel(logging.INFO)
            fh = logging.FileHandler(os.path.join(local_path, 'error'))
            print('Error! ' + str(exc))
            fh.setLevel(logging.INFO)
            logger.addHandler(fh)
            logger.exception(exc)
            capture_exception(exc)
            SequencingRun.objects.filter(
                pk=sub_sequencing_run.pk
            ).update(status='Error')


def sample_sheet(
        blob_client: BlockBlobService,
        container_name: str,
        run_name: str,
        file_names: list,
        max_samples: int = 40):
    """
    Download the SampleSheet.csv from the blob container and parse it to
    determine the number of samples and the number of sub-runs required
    :param blob_client: BlockBlobService object
    :param container_name: Name of the blob container
    :param run_name: Name of the sequencing run
    :param file_names: List of file names in the blob container
    :param max_samples (int): Maximum number of samples allowed in a sub-run.
    Default is 40
    :return: Dictionary containing the samples for each sub-run
    """
    # Download the SampleSheet.csv from the blob container
    blob_client.get_blob_to_path(
        container_name=container_name,
        blob_name='SampleSheet.csv',
        file_path=os.path.join(
            'olc_webportalv2',
            'media',
            run_name,
            'SampleSheet.csv'
        )
    )
    # Parse the SampleSheet.csv to determine the number of samples and the
    # number of sub-runs required
    sample_sheet_path = os.path.join(
        'olc_webportalv2',
        'media',
        run_name,
        'SampleSheet.csv'
    )
    print(
        'Downloading sample sheet to {sample_sheet_path}'
        .format(sample_sheet_path=sample_sheet_path)
    )

    # Rename the NextSeq SampleSheet.csv file
    new_file_name = 'NextSeqSampleSheet.csv'
    new_path = os.path.join(
        os.path.dirname(sample_sheet_path),
        new_file_name
    )

    # Check to see if the sample sheet is a NextSeq-style
    nextseq = check_sample_sheet_format(
        file_path=sample_sheet_path
    )

    # Reformat the sample sheet
    if nextseq:
        # Rename the file
        os.rename(sample_sheet_path, new_path)

        # Convert a NextSeq-style sample sheet to one that is more
        # easily parseable
        convert_sample_sheet(
            nextseq_sample_sheet=new_path,
            output_file_path=sample_sheet_path
        )
    # Initialize an empty list to store the header lines
    header = []
    # Initialize an empty list to store the sample names
    sample_names = []
    # Open the sample sheet file in read mode with utf-8 encoding
    with open(sample_sheet_path, 'r', encoding='utf-8') as samplesheet:
        # Create a CSV reader object
        reader = csv.reader(samplesheet)
        # Convert the reader object to a list of lines
        lines = list(reader)
        # Initialize a flag to indicate whether we're in the body of the file
        body = False
        # Iterate over each line in the file
        for line in lines:
            # If we're not in the body of the file
            if not body:
                # Append the line to the header
                header.append(line)
                # Iterate over each sub-line in the line
                for sub_line in line:
                    # If the subline contains 'Sample_ID'
                    if 'Sample_ID' in sub_line:
                        # Set the flag to indicate that we're in the body of
                        # the file
                        body = True
            # If we're in the body of the file
            else:
                # Append the line to the sample names
                sample_names.append(line)
    # Count the number of samples
    num_samples = len(sample_names)
    print(
        'There are {num_samples} samples in the run'
        .format(num_samples=num_samples)
    )
    # Perform floor division to determine the number of sub-runs required
    # e.g. 192 samples / 40 samples = 4 sub-runs
    num_sub_runs = num_samples // max_samples
    # Calculate the remainder of the division to determine if there are any
    # samples left over that will be added to an additional sub-run
    # e.g. 192 samples % 40 samples = 32 samples, which does not equal zero,
    # so add an additional sub-run
    if num_samples % max_samples != 0:
        num_sub_runs += 1
    print(
        'The run will be split into {num_sub_runs} sub-runs'
        .format(num_sub_runs=num_sub_runs)
    )
    # Create a dictionary to hold the samples for each sub-run
    sub_runs = {i + 1: [] for i in range(num_sub_runs)}
    # Create a copy of the sub_runs dictionary
    sub_runs_copy = {i + 1: [] for i in range(num_sub_runs)}
    # Distribute the samples across the sub-runs
    for i, sample in enumerate(sorted(sample_names)):
        # Calculate the sub-run index
        sub_run_index = i // max_samples
        # Add the sample to the appropriate sub-run
        sub_runs[sub_run_index + 1].append(sample[0])
        # Add the full line to the appropriate sub-run
        sub_runs_copy[sub_run_index + 1].append(sample)
    # Create and upload the sub-sample sheets to the blob container
    create_sub_sample_sheet(
        blob_client=blob_client,
        sub_runs=sub_runs_copy,
        container_name=container_name,
        header=header,
        file_path=os.path.join('olc_webportalv2', 'media', run_name),
        file_names=file_names
    )
    return sub_runs


def create_sub_sample_sheet(
        blob_client: BlockBlobService,
        container_name: str,
        file_names: list,
        file_path: str,
        header: list,
        sub_runs: dict):
    """
    Create a sub-sample sheet for each sub-run
    :param blob_client: BlockBlobService object
    :param container_name: Name of the blob container
    :param file_names: List of file names in the blob container
    :param file_path: Path to the sub-sample sheets
    :param header: Header of the SampleSheet.csv
    :param sub_runs: Dictionary containing the samples for each sub-run
    """
    # Set the path to the sub-sample sheets
    sample_sheet_path = os.path.join(file_path, 'sub_sample_sheets')
    # Iterate over the sub-runs and create the sub-sample sheets
    for i, sub_run in sub_runs.items():
        print('Creating sub-sample sheet for sub-run {i}'.format(i=i))
        sub_sample_sheet = header + sub_run
        sub_sample_sheet_path = sample_sheet_path + '-{i}'.format(i=i)
        # Create the sub-sample sheet path if required
        os.makedirs(sub_sample_sheet_path, exist_ok=True)
        with open(
                os.path.join(
                    sub_sample_sheet_path,
                    'SampleSheet.csv'
                ),
                'w',
                newline='',
                encoding='utf-8') as subsamplesheet:
            writer = csv.writer(subsamplesheet)
            writer.writerows(sub_sample_sheet)

        # Create the sub-container
        print('Creating sub-container for sub-run {i}'.format(i=i))
        sub_container_name = create_sub_container(
            blob_client=blob_client,
            container_name=container_name,
            i=i,
        )
        # # Create a generator of all the blobs in the container
        # blobs = blob_client.list_blobs(container_name=sub_container_name)

        # # Set the name of the archive
        # archive_name = sub_sample_sheet_path + '.zip'

        # # Check to see if the archive already exists in the sub-container
        # if os.path.basename(archive_name) in [blob.name for blob in blobs]:
        #     print(
        #         'Archive already present in container. '
        #         'Skipping archive creation'
        #     )
        #     continue
        # Iterate over the samples in the sub-run and copy the FASTQ files to
        # the sub-container
        for sample in sorted(sub_run):
            # Extract the sample name from the sample line
            sample_name = sample[0]
            print(
                'Processing sample {sample_name}'
                .format(sample_name=sample_name)
            )
            # Copy the FASTQ files for the sample to the sub-container
            copy_blobs(
                blob_client=blob_client,
                container_name=container_name,
                sample_name=sample_name,
                file_names=file_names,
                sub_container_name=sub_container_name,
            )

        # Upload the sample sheet to the sub-container
        print(
            'Uploading sample sheet to sub-container {sub_container_name}'
            .format(sub_container_name=sub_container_name)
        )
        blob_client.create_blob_from_path(
            container_name=sub_container_name,
            blob_name='SampleSheet.csv',
            file_path=os.path.join(
                sub_sample_sheet_path,
                'SampleSheet.csv'
            )
        )

        # # Wait briefly to ensure that all files are written to disk
        # sleep(1)

        # Create an archive of the FASTQ files and upload it to the
        # sub-container
        # archive_sub_run(
        #     blob_client=blob_client,
        #     local_path=sub_sample_sheet_path,
        #     sub_container_name=sub_container_name
        # )


# def verify_archive(
#     sub_sample_sheet_path: str
# ):
#     """
#     Verify that the archive was created successfully
#     :param blob_client: BlockBlobService object
#     :param container_name: Name of the blob container
#     :param archive_name: Name of the archive file
#     """
#     # Verify the archive after creation
#     archive = sub_sample_sheet_path + '.zip'
#     try:
#         with zipfile.ZipFile(archive, 'r') as zf:
#             bad_file = zf.testzip()
#             if bad_file:
#                 logging.error("Corrupted file in archive: %s", bad_file)
#     except zipfile.BadZipFile:
#         logging.error("Archive %s is corrupted!", archive)


def create_sub_container(
        blob_client: BlockBlobService,
        container_name: str,
        i: int):
    """
    Create a sub-container for the sub-run
    :param blob_client: BlockBlobService object
    :param container_name: Name of the blob container
    :param i: Index of the sub-run
    :return: Name of the sub-container
    """
    # Create the new container for the sub-run
    sub_container_name = container_name + '-{i}'.format(i=i)
    blob_client.create_container(sub_container_name)

    return sub_container_name


def copy_blobs(
    blob_client: BlockBlobService,
    container_name: str,
    file_names: list,
    sample_name: str,
    sub_container_name: str,
):
    """
    Copy blobs from a container to a sub-container
    :param blob_client: BlockBlobService object
    :param container_name: Name of the blob container
    :param file_names: List of file names in the blob container
    :param sample_name: Name of the sample
    :param sub_container_name: Name of the sub-container
    """
    # Extract the FASTQ files for the samples
    fastq_files = [fastq for fastq in file_names if sample_name in fastq]
    print(
        'FASTQ files for sample {sample_name}: {fastq_files}'
        .format(sample_name=sample_name, fastq_files=fastq_files)
    )
    # Iterate over the FASTQ files and copy them to the sub-container
    for fastq in fastq_files:
        # Generate a SAS token for the source blob
        sas_token = blob_client.generate_blob_shared_access_signature(
            container_name=container_name,
            blob_name=fastq,
            permission=BlobPermissions.READ,
            expiry=datetime.utcnow() + timedelta(hours=1),
        )
        source_url = blob_client.make_blob_url(
            container_name=container_name,
            blob_name=fastq,
            sas_token=sas_token
        )

        # Use copy_blob with the source_url
        blob_client.copy_blob(sub_container_name, fastq, source_url)


def archive_sub_run(
        blob_client: BlockBlobService,
        local_path: str,
        sub_container_name: str):
    """
    Create an archive of the FASTQ files (and sample sheet if present) for the
    sub-run and upload it to the destination
    """
    # Create an archive of all the FASTQ files (and the sample sheet if
    # present)
    print(
        'Creating archive of sub-run {sub_container_name}'
        .format(sub_container_name=sub_container_name)
    )
    shutil.make_archive(local_path, 'zip', local_path)
    # Set the name of the archive
    archive = local_path + '.zip'
    # Set the name of the blob file
    blob_file = os.path.basename(archive)
    # Upload the archive to the destination container
    print('Upload {archive} to sub-container {sub_container_name}'.format(
        archive=archive,
        sub_container_name=sub_container_name
    ))
    blob_client.create_blob_from_path(
        container_name=sub_container_name,
        blob_name=blob_file,
        file_path=archive
    )
    # Remove the archive
    os.remove(archive)
    # Use glob to find all FASTQ files in the local path
    fastq_files = glob(os.path.join(local_path, '*.fastq.gz'))
    # Delete the FASTQ files from the local path
    for fastq in fastq_files:
        os.remove(fastq)


def no_sample_sheet(
        blob_client: BlockBlobService,
        blob_files: list,
        container_name: str,
        max_samples: int = 40):
    """
    Count the number of samples in a blob container and distribute the
    samples across sub-runs
    :param blob_client: BlockBlobService object
    :param blob_files: List of file names in the blob container
    :param container_name: Name of the blob container
    :param max_samples: Maximum number of samples per sub-run
    :return: Dictionary containing the samples for each sub-run
    """
    # Extract the sample names from the blob files
    sample_names = {
        fastq.split('_')[0] for fastq in blob_files if fastq.endswith('.gz')
        }
    # Count the number of samples
    num_samples = len(sample_names)
    print(
        'There are {num_samples} samples in the run'
        .format(num_samples=num_samples)
    )
    # Perform floor division to determine the number of sub-runs required
    # e.g. 125 samples / 50 samples = 2 sub-runs
    num_sub_runs = num_samples // max_samples
    # Calculate the remainder of the division to determine if there are any
    # samples left over that will be added to an additional sub-run
    if num_samples % max_samples != 0:
        num_sub_runs += 1
    print(
        'The run will be split into {num_sub_runs} sub-runs'
        .format(num_sub_runs=num_sub_runs)
    )
    # Create a dictionary to hold the samples for each sub-run
    sub_runs = {i + 1: [] for i in range(num_sub_runs)}

    # Distribute the samples across the sub-runs
    for i, sample in enumerate(sorted(sample_names)):
        # Calculate the sub-run index
        sub_run_index = i // max_samples
        # Add the sample to the appropriate sub-run
        sub_runs[sub_run_index + 1].append(sample)

    # Copy the FASTQ files for the samples to sub-containers
    for i, sub_run in sub_runs.items():
        print('Processing sub-run {i}'.format(i=i))
        # Create the sub-container
        sub_container_name = create_sub_container(
            blob_client=blob_client,
            container_name=container_name,
            i=i,
        )
        # # Create a local path for the sub-run
        # local_path = os.path.join(
        #     'olc_webportalv2',
        #     'media',
        #     run_name,
        #     'sub_sample_sheets-{i}'.format(i=i)
        # )
        # os.makedirs(local_path, exist_ok=True)
        # # Check to see if the .zip file already exists in the sub-container
        # blobs = blob_client.list_blobs(container_name=sub_container_name)
        # # Set the name of the archive
        # archive_name = local_path + '.zip'
        # print(os.path.basename(archive_name), [blob.name for blob in blobs])
        # # Check if the archive is already present in the container
        # if os.path.basename(archive_name) in [blob.name for blob in blobs]:
        #     print(
        #         'Archive already present in container. '
        #         'Skipping archive creation'
        #     )
        #     continue
        # print([blob.name for blob in blobs], archive_name)
        for sample_name in sorted(sub_run):
            # Copy the FASTQ files for the sample to the sub-container
            copy_blobs(
                blob_client=blob_client,
                container_name=container_name,
                sample_name=sample_name,
                file_names=blob_files,
                sub_container_name=sub_container_name,
            )
        # Create an archive of the FASTQ files and upload it to the
        # sub-container
        # archive_sub_run(
        #     blob_client=blob_client,
        #     local_path=local_path,
        #     sub_container_name=sub_container_name
        # )

    return sub_runs


def send_email(subject, body, recipient):
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
        except smtplib.SMTPDataError as e:
            # If an SMTP data error occurs...
            if e.smtp_code == 554 and b"Access denied" in e.smtp_error:
                # If the error is an "Access denied" error, print a message
                # and wait for 5 seconds before retrying
                print("Access denied error occurred, retrying...")
                sleep(5)
            else:
                # If it's a different error, re-raise it
                raise
        except smtplib.SMTPServerDisconnected as e:
            # If the SMTP server gets disconnected...
            if "wrong version number" in str(e):
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


def load_report(report, seq_run):
    """
    Load the summary report into the SequencingRun model
    :param report: Path to the summary report
    :param seq_run: SequencingRun object
    :return: None if the report does not exist
    """
    if not os.path.isfile(report):
        seq_run.status = 'Error'
        seq_run.errors.append('Summary report not found in blob storage.')
        seq_run.save()
        return None
    summary_dict = pd.read_csv(
        report,
        names=[
            'SeqID',
            'SampleName',
            'Genus',
            'E_coli_Serotype',
            'SISTR_serovar',
            'GeneSeekr_Profile',
            'Vtyper_Profile',
            'rMLST_Result',
            'MLST_Result',
            'N50',
            'NumContigs',
            'TotalLength',
            'AverageCoverageDepth',
            'ConfindrContamSNVs',
            'SequencingDate',
            'Analyst',
            'Flowcell',
            'MachineName',
            'AssemblyDate',
            'PipelineVersion',
            'Database'
        ],
        sep=',').fillna('ND').transpose().to_dict()
    SummaryMetadata.objects.update_or_create(
        sequencing_run=seq_run,
        summary_results=summary_dict)

    return True


@shared_task
def cowbat_cleanup(sequencing_run_pk: int):
    """
    Perform clean up tasks for a completed sequencing run
    :param sequencing_run_pk: Primary key of the SequencingRun object
    """
    sequencing_run = SequencingRun.objects.get(pk=sequencing_run_pk)
    print('Cleaning up run {}'.format(sequencing_run.run_name))
    # With the sequencing run done, need to put create a zipfile with
    # assemblies and reports for user to download.
    # First create a folder.
    run_folder = os.path.join(
        'olc_webportalv2',
        'media',
        str(sequencing_run)
    )
    reports_and_assemblies_folder = os.path.join(
        'olc_webportalv2',
        'media',
        str(sequencing_run),
        'reports_and_assemblies'
    )
    if not os.path.isdir(reports_and_assemblies_folder):
        os.makedirs(reports_and_assemblies_folder)
    container_name = \
        sequencing_run.run_name.lower().replace('_', '-')
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Download all reports and assemblies to reports and assemblies folder.
    assemblies_folder = os.path.join(
        'olc_webportalv2',
        'media',
        str(sequencing_run),
        'reports_and_assemblies',
        'BestAssemblies'
    )

    reports_folder = os.path.join(
        'olc_webportalv2',
        'media',
        str(sequencing_run),
        'reports_and_assemblies',
        'reports'
    )
    if not os.path.isdir(assemblies_folder):
        os.makedirs(assemblies_folder)
    if not os.path.isdir(reports_folder):
        os.makedirs(reports_folder)
    print('Downloading reports and assemblies')
    # List all the things in the container - if it's a file in reports folder
    # or an assembly, download it.
    blobs = list(blob_client.list_blobs(container_name=container_name))
    blob_filenames = [b.name for b in blobs]
    for blob in blobs:
        if fnmatch.fnmatch(blob.name, os.path.join("BestAssemblies", "*.fasta")):
            blob_client.get_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(assemblies_folder, os.path.split(blob.name)[1]),
            )
        elif fnmatch.fnmatch(blob.name, os.path.join("reports", "*.csv")):
            blob_client.get_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(reports_folder, os.path.split(blob.name)[1]),
            )
        elif fnmatch.fnmatch(blob.name, os.path.join("reports", "*.tsv")):
            blob_client.get_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(reports_folder, os.path.split(blob.name)[1]),
            )
        elif fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'reports',
                '*.fa'
            )
        ):
            blob_client.get_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    reports_folder,
                    os.path.split(blob.name)[1]
                )
            )
        elif fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'reports',
                '*.xlsx'
            )
        ):
            blob_client.get_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    reports_folder,
                    os.path.split(blob.name)[1]
                )
            )
        # Also get the SampleSheet put into the reports folder.
        elif fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'SampleSheet.csv'
            )
        ):
            blob_client.get_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    reports_folder,
                    os.path.split(blob.name)[1]
                )
            )

    # update combinedMetadata.csv with read‑filenames
    add_read_filenames_to_metadata(
        sequencing_run=sequencing_run,
        blob_client=blob_client,
        container_name=container_name,
        reports_folder=reports_folder,
        blob_filenames=blob_filenames,
    )
    print('Files downloaded: ' + str(os.listdir(reports_folder)))
    # Load the necessary reports into the SequencingRun model
    report_complete = load_report(
        report=os.path.join(
            reports_folder,
            'summaryMetadata.csv'
        ),
        seq_run=SequencingRun.objects.get(pk=sequencing_run_pk)
    )
    # With that done, create a zipfile.
    blob_name = sequencing_run.container + '.zip'
    shutil.make_archive(
        os.path.join(
            run_folder,
            sequencing_run.container
        ),
        'zip',
        reports_and_assemblies_folder
    )
    report_assembly_container = 'reports-and-assemblies'
    sas_url = generate_download_link(
        blob_client=blob_client,
        container_name=report_assembly_container,
        output_zipfile=os.path.join(
            run_folder,
            blob_name
        ),
        expiry=730
    )
    print('Reports and assemblies should be here: ' + sas_url)
    SequencingRun.objects.filter(
        pk=sequencing_run_pk
    ).update(download_link=sas_url)
    shutil.rmtree(
        os.path.join(
            'olc_webportalv2',
            'media',
            str(sequencing_run)
        )
    )

    print('Loading of report complete: ' + str(report_complete))

    # Break if the report could not be loaded
    if not report_complete:
        return

    # Run is now considered complete! Update to let user know and send email
    # to people that need to know.
    try:
        seq_run = SequencingRun.objects.get(pk=sequencing_run_pk)
        if seq_run.status != 'Error':
            SequencingRun.objects.filter(
                pk=sequencing_run_pk
            ).update(status='Complete')
    except ObjectDoesNotExist:
        pass
    # Finally (but actually this time) send emails to relevant people to let
    # them know that things have worked.
    realtime_strains = []
    for seqid in sequencing_run.realtime_strains:
        if sequencing_run.realtime_strains[seqid] == 'True':
            realtime_strains.append(seqid)
    if settings.ENVIRONMENT == 'PROD':
        recipient_list = [
            'catherine.carrillo@inspection.gc.ca',
            'monique.arts@inspection.gc.ca',
            'adam.koziol@inspection.gc.ca',
            'ashley.cooper@inspection.gc.ca',
            'bridgette.kelly@inspection.gc.ca'
        ]
        for recipient in recipient_list:
            if recipient in {
                'ashley.cooper@inspection.gc.ca',
                'bridgette.kelly@inspection.gc.ca'
            }:
                body = (
                    'Please download the blob container to local '
                    'OLC storage. '
                )
            elif recipient == 'monique.arts@inspection.gc.ca':
                body = (
                    'Please add this data to the OLC database. '
                )
            else:
                body = ''

            body += (
                'Reports and assemblies are available at the '
                'following link: {}\n'.format(sas_url)
            )
            if realtime_strains:
                body += (
                    'In this run, the following strains will need '
                    'ROGAs created: {}'.format(realtime_strains)
                )
            print(recipient)
            print(body)

            # Attempt to send the emails up to 50 times
            send_email(
                subject='Run {} has finished assembly.'.format(
                    str(sequencing_run)),
                body=body,
                recipient=recipient
            )


def add_read_filenames_to_metadata(
    sequencing_run: SequencingRun,
    blob_client: BlockBlobService,
    container_name: str,
    reports_folder: str,
    blob_filenames: list,
) -> None:
    """
    Add two columns, ``R1_file`` and ``R2_file``, to
    ``combinedMetadata.csv`` and upload the modified file to the run container.

    ``blob_filenames`` should be a simple list of all blob names in the
    container (typically the result of ``[b.name for b in blobs]``).

    The columns are only created once; if they already exist we simply update
    empty cells and leave existing data alone.  Any seqid for which the
    forward/reverse lists are empty is recorded in
    ``sequencing_run.errors``.
    """
    csv_name = "combinedMetadata.csv"
    csv_path = os.path.join(reports_folder, csv_name)
    if not os.path.isfile(csv_path):
        # nothing to do if the report is not present
        return
    
    print("All blob filenames: " + str(blob_filenames))

    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:  # pragma: no cover
        sequencing_run.errors.append(
            "could not read {csv_name}: {exc}".format(csv_name=csv_name, exc=exc)
        )
        sequencing_run.save()
        return

    # convert any existing columns to string/object and replace NaN with ''
    for col in ('R1_file', 'R2_file'):
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str)

    # add columns if necessary
    added = False
    if "R1_file" not in df.columns:
        df["R1_file"] = ""
        added = True
    if "R2_file" not in df.columns:
        df["R2_file"] = ""
        added = True

    if not added:
        print("Columns already present, just updating empty cells")
    
    # populate
    for idx, row in df.iterrows():
        seqid = str(row.get("SeqID", "")).strip()
        print("Processing seqid: " + seqid)
        forwards = fnmatch.filter(blob_filenames, seqid + "*_R1*.fastq.gz")
        # remove trimmed, repaired, corrected, or otherwise modified reads from consideration
        forwards = [f for f in forwards if not any(x in f for x in ["trimmed", "repaired", "corrected"])]
        reverses = fnmatch.filter(blob_filenames, seqid + "*_R2*.fastq.gz")
        # remove trimmed, repaired, corrected, or otherwise modified reads from consideration
        reverses = [f for f in reverses if not any(x in f for x in ["trimmed", "repaired", "corrected"])]
        
        print(seqid, forwards, reverses)

        # only write into blank cells (after the fillna() above a blank cell is
        # exactly the empty string)
        if df.at[idx, 'R1_file'] == '':
            print("Updating R1_file for seqid {seqid}: {forwards}".format(seqid=seqid, forwards=forwards))
            if len(forwards) == 1:
                df.at[idx, 'R1_file'] = forwards[0]
            elif len(forwards) > 1:
                df.at[idx, 'R1_file'] = ';'.join(forwards)
        if df.at[idx, 'R2_file'] == '':
            print("Updating R2_file for seqid {seqid}: {reverses}".format(seqid=seqid, reverses=reverses))
            if len(reverses) == 1:
                df.at[idx, 'R2_file'] = reverses[0]
            elif len(reverses) > 1:
                df.at[idx, 'R2_file'] = ';'.join(reverses)

        if len(forwards) != 1 or len(reverses) != 1:
            sequencing_run.errors.append(
                "read‑pair problem for {seqid}: forwards={forwards}, "
                "reverses={reverses}".format(
                    seqid=seqid, forwards=forwards, reverses=reverses
                )
            )

    print("Final dataframe to write back:\n" + str(df))
    # write back and upload
    try:
        df.to_csv(csv_path, index=False)
        blob_client.create_blob_from_path(
            container_name=container_name,
            blob_name=os.path.join("reports", csv_name),
            file_path=csv_path,
        )
        print("Updated {csv_name} with read filenames and uploaded to blob storage".format(csv_name=csv_name))

    except Exception as exc:  # pragma: no cover
        sequencing_run.errors.append(
            "error writing/uploading {csv_name}: {exc}".format(
                csv_name=csv_name, exc=exc
            )
        )
    sequencing_run.save()


def escape_ansi(line):
    """
    Remove ANSI escape characters from a string
    :param line: String potentially containing ANSI escape characters
    :return: String with ANSI escape characters removed
    """
    # Use a regular expression to compile ANSI escape characters
    ansi_escape = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')
    # Substitute the escape characters with an empty string
    return ansi_escape.sub('', line)


def check_cowbat_progress(
    batch_client,
    batch_job_name,
    batch_task_name,
    sequencing_run
):
    """
    Check the progress of a cowbat job
    :param batch_client: Azure batch client
    :param batch_job_name: Name of the batch job
    :param sequencing_run: SequencingRun object
    """
    print('Checking node files')
    node_files = batch_client.file.list_from_task(
        job_id=batch_job_name,
        task_id=batch_task_name,
        recursive=True
    )
    print('Node files: ' + str([node_file.name for node_file in node_files]))
    # Ensure that the model status is set to "Processing"
    sequencing_run.status = 'Processing'
    sequencing_run.save()
    node_files = batch_client.file.list_from_task(
        job_id=batch_job_name,
        task_id=batch_task_name,
        recursive=True
    )
    contents = {}
    text_files = {}
    out_dir = os.path.join(
        'olc_webportalv2',
        'media',
        'cowbat',
        batch_job_name
    )
    os.makedirs(out_dir, exist_ok=True)
    try:
        for node_file in node_files:
            # Stderr.txt file
            if 'stderr' in node_file.name:
                try:
                    contents[node_file.name] = \
                        batch_client.file.get_from_task(
                            job_id=batch_job_name,
                            task_id=batch_task_name,
                            file_path=node_file.name
                        )
                    text_files[node_file.name] = \
                        batch_client.file.get_from_task(
                            job_id=batch_job_name,
                            task_id=batch_task_name,
                            file_path=node_file.name
                        )
                except Exception as exc:
                    sequencing_run.errors.append('stderr issue:')
                    sequencing_run.errors.append(exc)
                    sequencing_run.save()
            elif 'log' in node_file.name or 'err' in node_file.name or \
                    'metadata.json' in node_file.name:
                try:
                    text_files[node_file.name] = \
                        batch_client.file.get_from_task(
                            job_id=batch_job_name,
                            task_id=batch_task_name,
                            file_path=node_file.name
                        )
                except Exception as exc:
                    sequencing_run.errors.append(
                        'log/err/metadata.json issue:'
                    )
                    sequencing_run.errors.append(exc)
                    sequencing_run.save()
            elif 'reports' in node_file.name and not \
                    node_file.name.endswith('reports'):
                try:
                    text_files[node_file.name] = \
                        batch_client.file.get_from_task(
                            job_id=batch_job_name,
                            task_id=batch_task_name,
                            file_path=node_file.name
                        )
                except Exception as exc:
                    sequencing_run.errors.append('Reports issue:')
                    sequencing_run.errors.append(node_file.name)
                    sequencing_run.errors.append(text_files)
                    sequencing_run.errors.append(type(exc))
                    sequencing_run.errors.append(exc)
                    sequencing_run.save()
    except BatchErrorException as exc:
        sequencing_run.errors.append('BatchErrorException:')
        sequencing_run.errors.append(exc)
        sequencing_run.save()

    # Otherwise, update the model
    for file_name, content_object in contents.items():
        for content_chunk in content_object:
            try:
                clean_line = escape_ansi(line=content_chunk.decode())
                final_line = clean_line.split('\n')[-2]
                status = ' '.join(final_line.split(' ')[2:])
                sequencing_run.progress = status
                sequencing_run.save()
            except Exception as exc:
                sequencing_run.errors.append('Update model error:')
                sequencing_run.errors.append(exc)
                sequencing_run.save()
    # Save the files
    try:
        for file_name, content_object in text_files.items():
            # The get_blob_to_path method gets angry if the containing folder
            # doesn't already exist
            parent_dir = os.path.split(file_name)[-2]
            os.makedirs(os.path.join(out_dir, parent_dir), exist_ok=True)
            with open(
                os.path.join(
                    out_dir,
                    parent_dir,
                    os.path.basename(file_name)
                ),
                'w',
                encoding='utf-8'
            ) as text_output:
                for content_chunk in content_object:
                    text_output.write(content_chunk.decode())
    except Exception as exc:
        sequencing_run.errors.append('Saving file error:')
        sequencing_run.errors.append(exc)
        sequencing_run.save()


def check_cowbat_tasks():
    """
    This function checks the status of cowbat tasks and handles
    errors and task completion accordingly.

    No parameters are required as the function operates on global
    AzureTask objects.
    """
    # Check for completed cowbat runs
    azure_tasks = AzureTask.objects.filter()

    # Create batch client to check on the status of runs
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY
    )
    batch_client = batch.BatchServiceClient(
        credentials, base_url=settings.BATCH_ACCOUNT_URL
    )

    for azure_task in azure_tasks:
        sequencing_run = SequencingRun.objects.get(
            pk=azure_task.sequencing_run.pk
        )
        batch_job_name = sequencing_run.job_id
        print(sequencing_run, batch_job_name)

        # Check if all tasks associated with this job have completed
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        except BatchErrorException as exc:
            sequencing_run.errors.append('Running task error:')
            sequencing_run.errors.append(exc)
            sequencing_run.save()

        # If tasks have completed, check exit codes
        if tasks_completed:
            # Handle specific error case
            if sequencing_run.status == 'Resize Error':
                handle_resize_error(
                    sequencing_run=sequencing_run,
                    batch_job_name=batch_job_name,
                    azure_task_id=azure_task.id
                )

            print('Tasks are complete for ' + str(azure_task.id))
            handle_task_completion(
                batch_client=batch_client,
                sequencing_run=sequencing_run,
                batch_job_name=batch_job_name,
                azure_task_id=azure_task.id
            )
        else:
            handle_incomplete_tasks(
                batch_client=batch_client,
                sequencing_run=sequencing_run,
                batch_job_name=batch_job_name,
                azure_task_id=azure_task.id
            )


def handle_resize_error(sequencing_run, batch_job_name, azure_task_id):
    """
    Handle specific error case when the status is 'Resize Error'.

    Parameters:
    sequencing_run (SequencingRun): The sequencing run object.
    batch_job_name (str): The name of the batch job.
    azure_task_id (int): The ID of the Azure task.
    """
    print('Pool not present due to resize error ' + batch_job_name)
    # Retry!
    if sequencing_run.nextseq:
        digits = extract_ending_digits(
            sequencing_run_name=str(sequencing_run)
        )

        # Remove the trailing dash followed by one or more digits from the
        # batch job name using re
        clean_batch_job_name = re.sub(r'-\d+$', '', batch_job_name)

        # Set the name of the local folder using the digits
        local_folder = os.path.join(
            'olc_webportalv2',
            'media',
            clean_batch_job_name,
            'sub_sample_sheets-{digits}'.format(digits=digits)
        )
    else:
        # Create a configuration file to be used by the Azure batch script.
        local_folder = os.path.join(
            'olc_webportalv2',
            'media',
            str(sequencing_run)
        )
    print('Resubmitting batch job ' + batch_job_name)

    # Resubmit the batch request
    submit_batch(
        run_folder=local_folder,
        sequencing_run=sequencing_run
    )
    print('Will now delete original task ' + str(azure_task_id))

    # Delete task, so we don't have to keep checking up on it.
    AzureTask.objects.filter(id=azure_task_id).delete()


def handle_task_completion(
        batch_client,
        sequencing_run,
        batch_job_name,
        azure_task_id):
    """
    Handle the case when tasks are complete.

    Parameters:
    batch_client (BatchServiceClient): The Azure Batch client.
    sequencing_run (SequencingRun): The sequencing run object.
    batch_job_name (str): The name of the batch job.
    azure_task_id (int): The ID of the Azure task.
    """
    exit_codes_good = True
    exit_code = 0
    # Determine the exit code
    try:
        for cloudtask in batch_client.task.list(batch_job_name):
            if cloudtask.execution_info.exit_code != 0:
                exit_code = cloudtask.execution_info.exit_code
                exit_codes_good = False
                sequencing_run.errors.append('Exit code issue: ')
                sequencing_run.errors.append(cloudtask.execution_info)
    except BatchErrorException as exc:
        sequencing_run.errors.append('Terminating task error:')
        sequencing_run.errors.append(exc)
        sequencing_run.save()
    # Get rid of job and pool, so we don't waste big $$$ and do cleanup/get
    # files downloaded in tasks.
    try:
        batch_client.job.delete(job_id=batch_job_name)
    except BatchErrorException as exc:
        sequencing_run.errors.append('Terminating job error:')
        sequencing_run.errors.append(exc)
        sequencing_run.save()
    try:
        batch_client.pool.delete(pool_id=sequencing_run.pool_id)
    except BatchErrorException as exc:
        sequencing_run.errors.append('Terminating pool error:')
        sequencing_run.errors.append(exc)
        sequencing_run.save()
    # Add the exit code to the sequencing run
    sequencing_run.exit_code = exit_code
    sequencing_run.save()
    if exit_codes_good:
        # Clean up the sequencing run
        try:
            cowbat_cleanup.apply_async(
                queue='cowbat',
                args=(sequencing_run.pk, )
            )
        except Exception as exc:
            sequencing_run.errors.append('Clean-up error:')
            sequencing_run.errors.append(exc)
            sequencing_run.save()
    else:
        # Something went wrong - update status to error so user knows.
        sequencing_run.status = 'Error'
        sequencing_run.errors.append('Exit code bad')
        sequencing_run.save()
    try:
        # Delete task, so we don't have to keep checking up on it.
        AzureTask.objects.filter(id=azure_task_id).delete()
    except Exception as exc:
        sequencing_run.errors.append('AzureTask deletion error:')
        sequencing_run.errors.append(exc)
        sequencing_run.save()


def handle_incomplete_tasks(
    batch_client,
    sequencing_run,
    batch_job_name,
    azure_task_id
):
    """
    Handle the case when tasks are not complete.

    Parameters:
    batch_client (BatchServiceClient): The Azure Batch client.
    sequencing_run (SequencingRun): The sequencing run object.
    batch_job_name (str): The name of the batch job.
    azure_task_id (int): The ID of the Azure task.
    """
    # Locate all the batch pools
    pools = batch_client.pool.list()
    # Determine whether the batch job is in the list of pools
    present = batch_job_name in [pool.id for pool in pools]
    if not present and sequencing_run.status == 'Resize Error':
        handle_resize_error(sequencing_run, batch_job_name, azure_task_id)
    # Recreate the pools generator
    pools = batch_client.pool.list()
    # Iterate over the pools
    for pool in pools:
        # Ensure that the current batch job is being evaluated
        if pool.id == batch_job_name:
            # Proceed if there were batch resize errors. These usually happen
            # due to the quota getting reached
            if pool.resize_errors:
                # Delete the pool, job, and task
                delete_pool_job(
                    batch_client=batch_client,
                    batch_job_name=batch_job_name
                )
                # Give the pool a chance to be deleted
                sleep(60)
                sequencing_run.status = 'Resize Error'
                sequencing_run.save()
    check_cowbat_progress(
        batch_client,
        batch_job_name,
        sequencing_run.task_id,
        sequencing_run
    )


def extract_ending_digits(sequencing_run_name):
    """
    Check if a string ends with a dash followed by one or two digits and
    extract those digits.

    :param sequencing_run_name: Input string
    :return: The ending digits if they exist, None otherwise
    """
    match = re.search(r'-([0-9]{1,2})$', sequencing_run_name)
    return int(match.group(1)) if match else None


def check_tree_tasks() -> None:
    """
    Check the status of tree tasks and handle their completion or failure.
    """
    # Fetch all tree tasks
    tree_tasks = TreeAzureRequest.objects.filter()

    # Create a batch client using shared key credentials
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )
    batch_client = batch.BatchServiceClient(
        credentials,
        base_url=settings.BATCH_ACCOUNT_URL
    )

    # Iterate over each tree task
    for tree_task in tree_tasks:
        # Fetch the corresponding tree object
        tree_object = Tree.objects.get(
            pk=tree_task.tree_request.pk
        )

        # Construct the batch job name
        batch_job_name = 'tree-{}'.format(tree_task.tree_request.pk)

        try:
            # Check if all tasks related to this job have completed
            tasks_completed = all(
                task.state == TaskState.completed
                for task in batch_client.task.list(batch_job_name)
            )
        except BatchErrorException as exc:
            # If job doesn't exist, update status to 'Error' and delete
            # the task
            Tree.objects.filter(
                pk=tree_task.tree_request.pk
            ).update(status='Error')
            TreeAzureRequest.objects.filter(
                id=tree_task.id
            ).delete()
            print(
                'Batch error for task {task}: {err}'.format(
                    task=tree_task.id,
                    err=exc
                )
            )
            continue

        # If tasks have completed, check if they were successful
        if tasks_completed:
            exit_codes_good = all(
                task.execution_info.exit_code == 0
                for task in batch_client.task.list(batch_job_name)
            )

            # Delete the job and pool to save resources
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)

            if exit_codes_good:
                # Create a blob client to handle Azure Blob Storage operations
                blob_client = BlockBlobService(
                    account_key=settings.AZURE_ACCOUNT_KEY,
                    account_name=settings.AZURE_ACCOUNT_NAME
                )

                # Download the output container to zip it
                download_container(
                    blob_service=blob_client,
                    container_name='{}-output'.format(batch_job_name),
                    output_dir=os.path.join(
                        'olc_webportalv2',
                        'media'
                    )
                )

                # Open the tree file and read the first line
                tree_file = os.path.join(
                    'olc_webportalv2', 'media',
                    'tree-{}'.format(tree_object.pk), 'mash.tree'
                )
                with open(tree_file, 'r', encoding='utf-8') as f:
                    tree_string = f.readline()

                # If there are diversitree strains, get their names
                if tree_object.number_diversitree_strains > 0:
                    diverse_strains = strainchoosr.pd_greedy(
                        tree=ete3.Tree(tree_file),
                        number_tips=tree_object.number_diversitree_strains,
                        starting_strains=[]
                    )
                    tree_object.seqids_diversitree = \
                        strainchoosr.get_leaf_names_from_nodes(diverse_strains)

                # Update the tree object and save it
                tree_object.newick_tree = tree_string.rstrip().replace("'", "")
                tree_object.save()

                # Delete the containers related to this tree task
                for suffix in ['', '-input', '-output']:
                    try:
                        blob_client.delete_container(
                            container_name='tree-{}{}'.format(
                                tree_object.pk,
                                suffix
                            )
                        )
                    except AzureMissingResourceHttpError:
                        pass

                # Prepare the output folder and remove the batch config file
                tree_output_folder = os.path.join(
                    'olc_webportalv2', 'media',
                    'tree-{}'.format(tree_object.pk)
                )
                os.remove(
                    os.path.join(tree_output_folder, 'batch_config.txt')
                )

                # Zip the output folder and upload it to the cloud
                shutil.make_archive(
                    tree_output_folder,
                    'zip',
                    tree_output_folder
                )
                tree_result_container = 'tree-{}'.format(tree_object.pk)
                sas_url = generate_download_link(
                    blob_client=blob_client,
                    container_name=tree_result_container,
                    output_zipfile='{}.zip'.format(tree_output_folder),
                    expiry=8
                )

                # Remove the output folder and the zip file
                shutil.rmtree(tree_output_folder)
                zip_folder = os.path.join(
                    'olc_webportalv2', 'media',
                    '{}.zip'.format(batch_job_name)
                )
                if os.path.isfile(zip_folder):
                    os.remove(zip_folder)

                # Update the tree object and save it
                tree_object.download_link = sas_url
                tree_object.status = 'Complete'
                tree_object.save()
            else:
                # If the tasks were not successful, update the status to
                # 'Error'
                Tree.objects.filter(
                    pk=tree_task.tree_request.pk
                ).update(status='Error')

            # Delete the task to avoid iterating over it again
            TreeAzureRequest.objects.filter(
                id=tree_task.id
            ).delete()


def check_amr_summary_tasks():
    """
    Check the status of AMR summary tasks in Azure Batch Service.
    Update the status of tasks and perform necessary cleanup.
    """
    # Fetch all AMR summary tasks
    amr_summary_tasks = AMRAzureRequest.objects.filter()

    # Create a batch client using shared key credentials
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )
    batch_client = batch.BatchServiceClient(
        credentials,
        base_url=settings.BATCH_ACCOUNT_URL
    )

    # Iterate over each AMR summary task
    for amr_task in amr_summary_tasks:
        # Fetch the corresponding AMR summary object
        amr_object = AMRSummary.objects.get(
            pk=amr_task.amr_request.pk
        )

        # Construct the batch job name
        batch_job_name = 'amrsummary-{}'.format(amr_task.amr_request.pk)

        # Assume all tasks related to this job have completed
        tasks_completed = True

        try:
            # Check if all tasks related to this job have completed
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        # If job doesn't exist, update status to 'Error' and delete the task
        except BatchErrorException:
            AMRSummary.objects.filter(
                pk=amr_task.amr_request.pk
            ).update(status='Error')
            AMRAzureRequest.objects.filter(
                id=amr_task.id
            ).delete()
            continue

        # If tasks have completed, check if they were successful
        if tasks_completed:
            exit_codes_good = True
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.execution_info.exit_code != 0:
                    exit_codes_good = False

            # Delete the job and pool to save resources
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)

            if exit_codes_good:
                # Generate an SAS URL and update the download link
                blob_client = BlockBlobService(
                    account_key=settings.AZURE_ACCOUNT_KEY,
                    account_name=settings.AZURE_ACCOUNT_NAME
                )

                # Download the output container to zip it
                download_container(
                    blob_service=blob_client,
                    container_name=batch_job_name,
                    output_dir='olc_webportalv2/media'
                )

                output_dir = 'olc_webportalv2/media/{}'.format(batch_job_name)
                if os.path.isfile(
                    os.path.join(
                        output_dir,
                        'batch_config.txt'
                    )
                ):
                    os.remove(os.path.join(output_dir, 'batch_config.txt'))
                shutil.make_archive(
                    output_dir,
                    'zip',
                    output_dir
                )

                amr_result_container = 'amrsummary-{}'.format(amr_object.pk)
                sas_url = generate_download_link(
                    blob_client=blob_client,
                    container_name=amr_result_container,
                    output_zipfile=output_dir + '.zip',
                    expiry=8
                )

                # Populate the AMRDetail model with results
                seq_amr_dict = dict()
                for seqid in amr_object.seqids:
                    seq_amr_dict[seqid] = dict()
                # Open the AMR summary CSV file
                with open(
                    os.path.join(
                        output_dir,
                        'reports',
                        'amr_summary.csv'
                    ),
                    encoding='utf-8'
                ) as csvfile:
                    # Create a CSV dictionary reader
                    reader = csv.DictReader(csvfile)

                    # Iterate over each row in the CSV file
                    for row in reader:
                        # Extract the sequence ID, gene, and location from
                        # the row
                        seqid = row['Strain']
                        gene = row['Gene']
                        location = row['Location']

                        # If the sequence ID is not already in the dictionary,
                        # add it
                        if seqid not in seq_amr_dict:
                            seq_amr_dict[seqid] = dict()

                        # Add the gene and its location to the dictionary for
                        # this sequence ID
                        seq_amr_dict[seqid][gene] = location

                # Iterate over each sequence ID and its associated AMR results
                # in the dictionary
                for seqid, amr_results in seq_amr_dict.items():
                    # Create a new AMRDetail object for each sequence ID
                    AMRDetail.objects.create(
                        amr_request=amr_object,
                        seqid=seqid,
                        amr_results=amr_results
                    )

                # Cleanup
                shutil.rmtree(output_dir)
                os.remove(output_dir + '.zip')
                amr_object.download_link = sas_url
                amr_object.status = 'Complete'
                amr_object.save()

            else:
                amr_object.status = 'Error'
                amr_object.save()

            # Delete the task
            AMRAzureRequest.objects.filter(id=amr_task.id).delete()


def check_vir_typer_tasks():
    """
    Check the status of VirusTyper tasks.
    """

    # Get all VirTyper tasks
    vir_typer_tasks = VirTyperAzureRequest.objects.filter()

    # Set up Batch service client
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
    batch_client = batch.BatchServiceClient(
        credentials, base_url=settings.BATCH_ACCOUNT_URL)

    # Iterate over each VirTyper task
    for sub_task in vir_typer_tasks:
        vir_typer_task = VirTyperProject.objects.get(
            pk=sub_task.project_name.pk)
        batch_job_name = VirTyperProject.objects.get(
            pk=vir_typer_task.pk).container_namer()

        # Check if tasks related with this VirusTyper project have finished
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        # Catch specific Azure Batch exceptions
        except batchmodels.BatchErrorException as exc:
            print('An error occurred: {}'.format(exc))
            VirTyperProject.objects.filter(
                pk=vir_typer_task.pk).update(status='Error')
            VirTyperAzureRequest.objects.filter(id=sub_task.id).delete()
            continue
        except Exception as exc:  # Catch all other exceptions
            print('An unexpected error occurred: {}'.format(exc))
            VirTyperProject.objects.filter(
                pk=vir_typer_task.pk).update(status='Error')
            VirTyperAzureRequest.objects.filter(id=sub_task.id).delete()
            continue

        # If tasks have completed, check if they were successful
        if tasks_completed:
            exit_codes_good = True
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.execution_info.exit_code != 0:
                    exit_codes_good = False

            # Delete job and pool to save resources
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)

            if exit_codes_good:
                # Set up Blob service client
                blob_client = BlockBlobService(
                    account_key=settings.AZURE_ACCOUNT_KEY,
                    account_name=settings.AZURE_ACCOUNT_NAME)

                vir_typer_result_container = batch_job_name

                # Download the output container to zip it
                download_container(
                    blob_service=blob_client,
                    container_name=vir_typer_result_container,
                    output_dir='olc_webportalv2/media')

                output_dir = os.path.join(
                    'olc_webportalv2',
                    'media',
                    batch_job_name
                )

                # Remove batch_config.txt if it exists
                batch_config_path = os.path.join(
                    output_dir,
                    'batch_config.txt'
                )
                if os.path.isfile(batch_config_path):
                    os.remove(batch_config_path)

                # Create a zip archive of the output directory
                shutil.make_archive(output_dir, 'zip', output_dir)

                # Read in the json output
                json_output = os.path.join(
                    output_dir,
                    'virus_typer_outputs.json'
                )
                with open(json_output, 'r', encoding='utf-8') as json_report:
                    vir_typer_task.report = json.load(json_report)

                # Generate a download link for the output zip file
                sas_url = generate_download_link(
                    blob_client=blob_client,
                    container_name=vir_typer_result_container,
                    output_zipfile=output_dir + '.zip',
                    expiry=8)

                vir_typer_task.download_link = sas_url
                vir_typer_task.status = 'Complete'
                vir_typer_task.save()

                # Clean up the output directory and zip file
                shutil.rmtree(output_dir)
                os.remove(output_dir + '.zip')
            else:
                vir_typer_task.status = 'Error'
                vir_typer_task.save()

            # Delete the Azure task
            VirTyperAzureRequest.objects.filter(id=sub_task.id).delete()


def check_prokka_tasks() -> None:
    """
    Check the status of Prokka tasks and handle them accordingly.
    """
    # Fetch all Prokka tasks
    prokka_tasks = ProkkaAzureRequest.objects.filter()

    # Create credentials for Azure Batch service
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )

    # Create a Batch service client
    batch_client = batch.BatchServiceClient(
        credentials,
        base_url=settings.BATCH_ACCOUNT_URL
    )

    # Iterate over each Prokka task
    for prokka_task in prokka_tasks:
        # Fetch the corresponding Prokka object
        prokka_object = ProkkaRequest.objects.get(
            pk=prokka_task.prokka_request.pk
        )

        # Create a job name for the batch
        batch_job_name = 'prokka-{}'.format(prokka_task.prokka_request.pk)

        # Assume all tasks are completed
        tasks_completed = True

        try:
            # Check the status of each task in the batch
            for cloud_task in batch_client.task.list(batch_job_name):
                # If any task is not completed, set tasks_completed to False
                if cloud_task.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        except (batchmodels.BatchErrorException, Exception) as exc:
            # Handle exceptions
            print('An error occurred: {}'.format(exc))
            ProkkaRequest.objects.filter(
                pk=prokka_task.prokka_request.pk
            ).update(status='Error')
            ProkkaAzureRequest.objects.filter(id=prokka_task.id).delete()
            continue

        # If all tasks are completed, handle them
        if tasks_completed:
            handle_completed_tasks(
                batch_client,
                batch_job_name,
                prokka_object,
                prokka_task
            )


def handle_completed_tasks(
    batch_client: batch.BatchServiceClient,
    batch_job_name: str,
    prokka_object: ProkkaRequest,
    prokka_task: ProkkaAzureRequest
) -> None:
    """
    Handle completed tasks.
    """
    # Assume all exit codes are good
    exit_codes_good = True

    # Check the exit code of each task
    for cloud_task in batch_client.task.list(batch_job_name):
        # If any exit code is not 0, set exit_codes_good to False
        if cloud_task.execution_info.exit_code != 0:
            exit_codes_good = False

    # Delete the job and the pool
    batch_client.job.delete(job_id=batch_job_name)
    batch_client.pool.delete(pool_id=batch_job_name)

    # If all exit codes are good, handle successful tasks
    if exit_codes_good:
        handle_successful_tasks(
            batch_job_name,
            prokka_object
        )
    else:
        # Otherwise, set the status of the Prokka object to 'Error'
        prokka_object.status = 'Error'
        prokka_object.save()

    # Delete the Prokka task
    ProkkaAzureRequest.objects.filter(id=prokka_task.id).delete()


def handle_successful_tasks(
    batch_job_name: str,
    prokka_object: ProkkaRequest
) -> None:
    """
    Handle successful tasks.
    """
    # Create a Blob service client
    blob_client = BlockBlobService(
        account_key=settings.AZURE_ACCOUNT_KEY,
        account_name=settings.AZURE_ACCOUNT_NAME
    )

    # Download the container
    download_container(
        blob_service=blob_client,
        container_name=batch_job_name,
        output_dir=os.path.join('olc_webportalv2', 'media')
    )

    # Define the output directory
    output_dir = os.path.join('olc_webportalv2', 'media', batch_job_name)

    # If a batch_config.txt file exists, remove it
    if os.path.isfile(os.path.join(output_dir, 'batch_config.txt')):
        os.remove(os.path.join(output_dir, 'batch_config.txt'))

    # Create a zip archive of the output directory
    shutil.make_archive(output_dir, 'zip', output_dir)

    # Define the result container name
    prokka_result_container = 'prokka-result-{}'.format(prokka_object.pk)

    # Generate a download link for the zip file
    sas_url = generate_download_link(
        blob_client=blob_client,
        container_name=prokka_result_container,
        output_zipfile=output_dir + '.zip',
        expiry=8
    )

    # Update the Prokka object with the download link and status
    prokka_object.download_link = sas_url
    prokka_object.status = 'Complete'
    prokka_object.save()

    # Remove the output directory and the zip file
    shutil.rmtree(output_dir)
    os.remove(output_dir + '.zip')


def get_batch_blast_results(
    blast_result_file,
    geneseekr_task
):
    """
    Parse BLAST results and update the GeneSeekr task.

    :param blast_result_file: File with BLAST results.
    :param geneseekr_task: GeneSeekr task to be updated.
    :return: None
    """
    # Parse query sequence to get query IDs
    query_names = [
        query.id for query in SeqIO.parse(
            StringIO(geneseekr_task.query_sequence), 'fasta'
        )
    ]

    # Update geneseekr_task with query names
    geneseekr_task.gene_targets = query_names
    geneseekr_task.save()

    # Initialize dictionary to track SeqIDs hits for each query gene
    gene_hits = {
        query_name: {seqid: 0.0 for seqid in geneseekr_task.seqids}
        for query_name in query_names
    }

    # Parse BLAST results file
    try:
        blast_results_dict = pd.read_csv(
            blast_result_file, sep=','
        ).fillna('ND').transpose()
    except pd.errors.ParserError:
        return

    # Update gene_hits with BLAST results
    for _, blast_dict in blast_results_dict.items():
        for query_name in query_names:
            gene_hits[query_name][blast_dict['Strain']] = \
                blast_dict[query_name]

    # Update GeneSeekrDetail objects with BLAST results
    for seqid in geneseekr_task.seqids:
        GeneSeekrDetail.objects.filter(
            geneseekr_request=geneseekr_task,
            seqid=seqid,
        ).delete()
        geneseekr_detail = GeneSeekrDetail.objects.create(
            geneseekr_request=geneseekr_task, seqid=seqid
        )
        geneseekr_detail.geneseekr_results = {
            query: gene_hits[query][seqid] for query in gene_hits
        }
        geneseekr_detail.save()

    # Calculate percentage of non-zero hits for each gene
    for query in gene_hits:
        num_hits = sum(
            1 for seqid in gene_hits[query] if gene_hits[query][seqid] != 0.0
        )
        percent_found = 100 * num_hits / len(geneseekr_task.seqids)
        geneseekr_task.geneseekr_results[query] = percent_found
    geneseekr_task.save()


def get_batch_blast_hits(run_folder, geneseekr_task):
    """
    Parse BLAST hits and update the GeneSeekr task.

    :param run_folder: Folder containing BLAST results.
    :param geneseekr_task: GeneSeekr task to be updated.
    :return: None
    """
    for seqid in geneseekr_task.seqids:
        results = glob(
            os.path.join(
                run_folder,
                '{seqid}*.tsv'.format(seqid=seqid))
        )[0]

        # Read results into a dictionary
        results_dict = pd.read_csv(
            results,
            names=[
                'query_id', 'subject_id', 'positives', 'mismatches',
                'gaps', 'evalue', 'bit_score', 'subject_length',
                'alignment_length', 'query_start', 'query_end',
                'subject_start', 'subject_end', 'percent_match',
                'query_sequence', 'subject_sequence'
            ],
            sep='\t'
        ).fillna('ND').transpose()

        for _, blast_result in results_dict.items():
            if blast_result.query_id == 'query_id':
                continue

            # Format contig name
            modified_contig_name = '{seqid}_{query_id}'.format(
                seqid=seqid,
                query_id=blast_result.query_id
            )

            # Remove any previously populated versions
            TopBlastHit.objects.filter(
                geneseekr_request=geneseekr_task,
                contig_name=modified_contig_name,
            ).delete()

            # Create a new TopBlastHit object
            top_blast_hit = TopBlastHit(
                contig_name=modified_contig_name,
                query_coverage=int(blast_result.alignment_length),
                percent_identity=blast_result.percent_match,
                start_position=blast_result.query_start,
                end_position=blast_result.query_end,
                e_value=blast_result.evalue,
                geneseekr_request=geneseekr_task,
                gene_name=blast_result.subject_id.replace('gb|', '')
                                                 .replace('|', ''),
                query_start_position=blast_result.subject_start,
                query_end_position=blast_result.subject_end,
                query_sequence_length=blast_result.subject_length
            )

            # Save the hit
            top_blast_hit.save()


def _populate_benchmark_seqids(
    *, blob_client, geneseekr_request, output_container, run_folder
):
    """
    Download benchmark SEQIDs and store them on a GeneSeekr request.

    The Batch task creates the file dynamically from extracted FASTA
    filenames and copies it to the root of the request container. Reading this
    generated file avoids maintaining static benchmark-ID files in the source
    tree and ensures the request reflects the exact archive that was analysed.

    Args:
        blob_client (BlockBlobService): Azure Blob service client.
        geneseekr_request (GeneSeekrRequest): Completed benchmark request.
        output_container (str): Request container containing the SEQID file.
        run_folder (str): Local folder used while processing results.

    Returns:
        list: Sorted sequence IDs loaded from the generated file.

    Raises:
        FileNotFoundError: If the generated SEQID blob is missing.
        ValueError: If the generated file does not contain any sequence IDs.
    """
    benchmark_name = normalise_benchmark_name(
        benchmark_name=geneseekr_request.benchmark
    )
    seqids_name = "{0}_seqids.txt".format(benchmark_name)

    if not blob_client.exists(
        container_name=output_container,
        blob_name=seqids_name,
    ):
        raise FileNotFoundError(
            "Generated benchmark SEQID file is missing: {0}/{1}".format(
                output_container,
                seqids_name,
            )
        )

    os.makedirs(run_folder, exist_ok=True)
    local_seqids = os.path.join(run_folder, seqids_name)
    blob_client.get_blob_to_path(
        container_name=output_container,
        blob_name=seqids_name,
        file_path=local_seqids,
    )

    with open(local_seqids, "r", encoding="utf-8") as seqids_object:
        sequence_ids = sorted({line.strip() for line in seqids_object if line.strip()})

    if not sequence_ids:
        raise ValueError(
            "Generated benchmark SEQID file is empty: {0}/{1}".format(
                output_container,
                seqids_name,
            )
        )

    geneseekr_request.seqids = sequence_ids
    geneseekr_request.save(update_fields=["seqids"])
    return sequence_ids


def check_geneseekr_tasks():
    """
    This function checks the status of GeneSeekr tasks and updates their
    status. It also handles errors, cleans up completed tasks, and sends
    emails to users.
    """

    # Get all GeneSeekr tasks
    geneseekr_tasks = GeneSeekrAzureRequest.objects.filter()

    # Create credentials for the batch client
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY
    )

    # Create a batch client with the credentials and account URL
    batch_client = batch.BatchServiceClient(
        credentials, base_url=settings.BATCH_ACCOUNT_URL
    )

    # Iterate over each GeneSeekr task
    for geneseekr_task in geneseekr_tasks:

        # Get the corresponding GeneSeekr request
        geneseekr_request = GeneSeekrRequest.objects.get(
            pk=geneseekr_task.geneseekr_request.pk
        )

        # Create a name for the batch job
        batch_job_name = 'geneseekr-{}'.format(
            geneseekr_task.geneseekr_request.pk
        )

        # Initialize a flag to check if all tasks have completed
        tasks_completed = True

        # Try to list all tasks for the batch job
        try:
            for cloudtask in batch_client.task.list(batch_job_name):

                # If any task is not completed, set the flag to False
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False

        # If a BatchErrorException occurs, handle it
        except batchmodels.BatchErrorException as e:
            print("A BatchErrorException occurred: {}".format(e))
            GeneSeekrRequest.objects.filter(
                pk=geneseekr_task.geneseekr_request.pk
            ).update(status='Error')
            GeneSeekrAzureRequest.objects.filter(id=geneseekr_task.id).delete()
            continue

        # If a general exception occurs, handle it
        except Exception as e:
            print("An exception occurred: {}".format(e))
            GeneSeekrRequest.objects.filter(
                pk=geneseekr_task.geneseekr_request.pk
            ).update(status='Error')
            GeneSeekrAzureRequest.objects.filter(id=geneseekr_task.id).delete()
            continue

        # If all tasks have completed, check if they were successful
        if tasks_completed:

            # Initialize a flag to check if all exit codes are 0 (successful)
            exit_codes_good = True

            # Iterate over each task again
            for cloudtask in batch_client.task.list(batch_job_name):

                # If any task's exit code is not 0, set the flag to False
                if cloudtask.execution_info.exit_code != 0:
                    print('Error!', cloudtask.execution_info.exit_code)
                    exit_codes_good = False

            # Delete the batch job and pool to save resources
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)

            # If all exit codes are good, proceed with the next steps
            if exit_codes_good:

                # Define the output container and run folder
                output_container = batch_job_name
                run_folder = os.path.join(
                    'olc_webportalv2',
                    'media',
                    batch_job_name
                )

                # Create a blob client to interact with Azure Blob Storage
                blob_client = BlockBlobService(
                    account_name=settings.AZURE_ACCOUNT_NAME,
                    account_key=settings.AZURE_ACCOUNT_KEY
                )

                # List all blobs in the output container
                blobs = blob_client.list_blobs(container_name=output_container)

                # Iterate over each blob
                for blob in blobs:

                    # If the blob is a CSV report, download it
                    if fnmatch.fnmatch(
                        blob.name,
                        os.path.join(
                            'reports',
                            'geneseekr_blastn.csv'
                        )
                    ):
                        blob_client.get_blob_to_path(
                            container_name=output_container,
                            blob_name=blob.name,
                            file_path=os.path.join(
                                run_folder,
                                os.path.split(blob.name)[1]
                            )
                        )

                    # If the blob is a TSV report, download it
                    elif fnmatch.fnmatch(blob.name, os.path.join(
                        'reports',
                        '*.tsv')
                    ):
                        blob_client.get_blob_to_path(
                            container_name=output_container,
                            blob_name=blob.name,
                            file_path=os.path.join(
                                run_folder,
                                os.path.split(blob.name)[1]
                            )
                        )

                # If the GeneSeekr request is a benchmark, update the seqids
                if geneseekr_request.benchmark:
                    _populate_benchmark_seqids(
                        blob_client=blob_client,
                        geneseekr_request=geneseekr_request,
                        output_container=output_container,
                        run_folder=run_folder,
                    )

                # Get BLAST results and hits
                get_batch_blast_results(
                    blast_result_file=os.path.join(
                        run_folder,
                        'geneseekr_blastn.csv'
                    ),
                    geneseekr_task=geneseekr_request
                )
                get_batch_blast_hits(
                    run_folder=run_folder,
                    geneseekr_task=geneseekr_request
                )

                # Generate an SAS url with read access that users will be able
                # to use to download their sequences.
                sas_token = \
                    blob_client.generate_container_shared_access_signature(
                        container_name=output_container,
                        permission=BlobPermissions.READ,
                        expiry=datetime.utcnow() +
                        timedelta(days=8)
                    )
                sas_url = blob_client.make_blob_url(
                    container_name=output_container,
                    blob_name='reports/geneseekr_blastn.xlsx',
                    sas_token=sas_token
                )
                sas_url_sequence = blob_client.make_blob_url(
                    container_name=output_container,
                    blob_name='reports/geneseekr_blastn.csv',
                    sas_token=sas_token
                )

                # Update request status and download links
                geneseekr_request.download_link = sas_url
                geneseekr_request.download_link_sequence = sas_url_sequence
                geneseekr_request.status = 'Complete'
                geneseekr_request.save()

                # Send email to users
                email_list = geneseekr_request.emails_array
                for email in email_list:
                    send_email(
                        subject='Geneseekr Query {} has finished.'.format(
                            str(geneseekr_request)
                        ),
                        body='This email is to inform you that the Geneseekr'
                        ' Query {} has completed and is available at the '
                        'following link {}'.format(
                            str(geneseekr_request),
                            sas_url
                        ),
                        recipient=email
                    )

                # Finally, do some cleanup
                shutil.rmtree(run_folder)
            else:
                print('Error in exit codes')
                geneseekr_request.status = 'Error'
                geneseekr_request.save()

            GeneSeekrAzureRequest.objects.filter(id=geneseekr_task.id).delete()


@shared_task()
def monitor_tasks():
    """
    Keep track of jobs that have been submitted to Azure Batch Service.
    Call each type of task we submit to Batch separately, and have sentry
    tell us if anything goes wrong.
    """
    # Check for completed cowbat runs
    try:
        check_cowbat_tasks()
    except Exception as e:
        capture_exception(e)

    try:
        check_nanopore_tasks()
    except Exception as exc:
        logger.exception("Nanopore task monitoring failed: {}".format(exc))
        capture_exception(exc)

    # Also check for Mash tree creation tasks
    try:
        check_tree_tasks()
    except Exception as e:
        capture_exception(e)

    # Next up - AMR summary requests.
    try:
        check_amr_summary_tasks()
    except Exception as e:
        capture_exception(e)
    # VirusTyper!
    try:
        check_vir_typer_tasks()
    except Exception as e:
        capture_exception(e)
    # Prokka!
    try:
        check_prokka_tasks()
    except Exception as e:
        capture_exception(e)
    # GeneSeekr!
    try:
        check_geneseekr_tasks()
    except Exception as e:
        capture_exception(e)
    # PrimerVerifier!
    try:
        check_verifier_tasks()
    except Exception as e:
        capture_exception(e)
    # PrimerValidator!
    try:
        check_validator_tasks()
    except Exception as e:
        capture_exception(e)
    # AmpliSeq!
    try:
        check_ampliseq_tasks()
    except Exception as exc:
        capture_exception(exc)
    # COWSNPhR
    try:
        check_cowsnphr_tasks()
    except Exception as exc:
        capture_exception(exc)


def generate_download_link(
    blob_client,
    container_name,
    output_zipfile,
    expiry=8
):
    """
    Make a download link for a file that will be put into Azure blob storage,
    good for up to expiry days
    :param blob_client: Instance of azure.storage.blob.BlockBlobService
    :param container_name: Name of container you want to create.
    :param output_zipfile: Zipfile you want to upload and create a link for.
    :param expiry: Number of days link should be valid for.
    :return: String of a link that allows people to download container.
    """
    # Create a new container in the blob storage
    blob_client.create_container(container_name)

    # Get the name of the file from the full path
    blob_name = os.path.split(output_zipfile)[1]

    # Upload the file to the blob storage
    blob_client.create_blob_from_path(
        container_name=container_name,
        blob_name=blob_name,
        file_path=output_zipfile
    )

    # Generate a shared access signature (SAS) token
    # This token allows read access to the container
    # It expires after a certain number of days
    sas_token = blob_client.generate_container_shared_access_signature(
        container_name=container_name,
        permission=BlobPermissions.READ,
        expiry=datetime.utcnow() + timedelta(days=expiry)
    )

    # Create a URL for the blob that includes the SAS token
    # This URL can be used to access the blob without the storage account key
    sas_url = blob_client.make_blob_url(
        container_name=container_name,
        blob_name=blob_name,
        sas_token=sas_token
    )

    # Return the SAS URL
    return sas_url


def download_container(blob_service, container_name, output_dir):
    """
    Download all files in a container to local storage
    Modified from:
    https://blogs.msdn.microsoft.com/brijrajsingh/2017/05/27/
    downloading-a-azure-blob-storage-container-python/
    """
    generator = blob_service.list_blobs(container_name)
    for blob in generator:
        # check if the path contains a folder structure, create the folder
        # structure
        if "/" in blob.name:
            # extract the folder path and check if that folder exists locally,
            # and if not create it
            head, tail = os.path.split(blob.name)
            if os.path.isdir(os.path.join(output_dir, head)):
                # download the files to this directory
                blob_service.get_blob_to_path(
                    container_name,
                    blob.name,
                    os.path.join(
                        output_dir,
                        head,
                        tail
                    )
                )
            else:
                # create the diretcory and download the file to it
                os.makedirs(os.path.join(output_dir, head))
                blob_service.get_blob_to_path(
                    container_name,
                    blob.name,
                    os.path.join(
                        output_dir,
                        head,
                        tail
                    )
                )
        else:
            blob_service.get_blob_to_path(
                container_name,
                blob.name,
                os.path.join(
                    output_dir,
                    blob.name
                )
            )


def check_sample_sheet_format(
    file_path: str,
    target_string: str = '[BCLConvert_Data]'
) -> bool:
    '''
    Check if a specific string exists in a sample sheet.

    Parameters:
    file_path (str): The path to the sample sheet.
    target_string (str): The string for which to search in the file.

    Returns:
    bool: True if the string is found, False otherwise.
    '''
    # Open the file with utf-8 encoding
    with open(file_path, 'r', encoding='utf-8') as file:
        # Read the file line by line
        for line in file:
            # Check if the target string is in the current line
            if target_string in line:
                # If found, return True
                return True
    # If the string is not found after reading the whole file, return False
    return False


def convert_sample_sheet(
    nextseq_sample_sheet: str,
    output_file_path: str
) -> None:
    """
    Convert a NextSeq-style sample sheet to a MiSeq-style sample sheet.

    Parameters:
    next_seq_sample_sheet (str): Path to the NextSeq sample sheet.
    output_file_path (str): Path to the output MiSeq sample sheet.

    Returns:
    None
    """
    # Define constants
    header_section = '[Header]'
    reads_section = '[Reads]'
    settings_section = '[Settings]'
    data_section = '[Data]'
    data_columns = [
        'Sample_ID',
        'Sample_Name',
        'Description',
        'I7_Index_ID',
        'index',
        'I5_Index_ID',
        'index2',
        'Sample_Project',
        'Sample_Plate',
        'Sample_Well'
    ]

    # Load the data frames using the read_sections function
    dataframes = read_sections(
        file_path=nextseq_sample_sheet
    )

    # Extract the necessary information
    experiment_name = dataframes['Header'].get('RunName', '')
    forward_read_length = dataframes['Reads'].get('Read1Cycles', '')
    reverse_read_length = dataframes['Reads'].get('Read2Cycles', '')
    adapter = "{adapter1}_{adapter2}".format(
        adapter1=dataframes['BCLConvert_Settings'].get('AdapterRead1', ''),
        adapter2=dataframes['BCLConvert_Settings'].get('AdapterRead2', '')
    )

    # Create the MiSeq sample sheet
    with open(output_file_path, 'w', encoding='utf-8') as file:
        file.write('{header}\n'.format(header=header_section))
        file.write('Experiment Name,{experiment}\n'.format(
            experiment=experiment_name))
        file.write('\n{reads}\n'.format(reads=reads_section))
        file.write('{forward}\n'.format(forward=forward_read_length))
        file.write('{reverse}\n'.format(reverse=reverse_read_length))
        file.write('\n{settings}\n'.format(settings=settings_section))
        file.write('adapter,{adapter}\n'.format(adapter=adapter))
        file.write('\n{data}\n'.format(data=data_section))
        file.write(','.join(data_columns) + '\n')
        for seq_id, seq_dict in sorted(dataframes['Cloud_Data'].items()):
            description = ''
            i7_index = dataframes['BCLConvert_Data'][seq_id].get('Index')
            i5_index = dataframes['BCLConvert_Data'][seq_id].get('Index2')
            sample_project = ''
            sample_plate = seq_dict.get('ProjectName', '')
            sample_well = ''
            file.write(
                '{sample_id},{sample_name},{description},{i7_id},{i7},'
                '{i5_id},{i5},{sample_project},{sample_plate},{sample_well}\n'
                .format(
                    sample_id=seq_id,
                    sample_name=seq_id,
                    description=description,
                    i7_id=i7_index,
                    i7=i7_index,
                    i5_id=i5_index,
                    i5=i5_index,
                    sample_project=sample_project,
                    sample_plate=sample_plate,
                    sample_well=sample_well
                )
            )


def read_sections(file_path: str) -> dict:
    """
    Read a CSV file with different sections into separate DataFrames.

    Parameters:
    file_path (str): The path to the CSV file.

    Returns:
    dict: A dictionary where the keys are the section names and the values
          are dictionaries with key-value pairs of header name and
          corresponding cell value.
    """

    # Check if the file exists
    if not os.path.exists(file_path):
        # If the file does not exist, raise an error
        raise FileNotFoundError(
            'The file {file_path} does not exist.'.format(file_path=file_path)
        )

    # Open the file in read mode with utf-8 encoding
    with open(file_path, 'r', encoding='utf-8') as file:
        # Read the entire content of the file
        content = file.read()

    # Split the content into sections
    sections = content.split('[')
    sections = sections[1:]  # Exclude the first empty section

    # Initialize an empty dictionary to store the dataframes
    dataframes = {}

    # Loop through each section
    for section in sections:
        # Split the section into lines
        lines = section.split('\n')
        # Extract the section name from the first line
        section_name = lines[0].replace(']', '').replace(',', '')
        # Join the remaining lines back into a string
        section_data = '\n'.join(lines[1:])
        # Read the section data into a dataframe
        # df = pd.read_csv(StringIO(section_data), sep=',', header=None)
        df = pd.read_csv(StringIO(section_data), sep=',', header=None)

        # Check the section name
        if section_name in ['BCLConvert_Data', 'Cloud_Data']:
            # Process 'BCLConvert_Data' or 'Cloud_Data'

            # Transpose the DataFrame so that the values in row 1 become
            # the keys
            df = df.transpose()

            # The first row will become the column names after transposing
            df.columns = df.iloc[0]

            # Drop the first row as it's now the column names
            df = df.drop(df.index[0])

            # Populate the dictionary with the section_name
            dataframes[section_name] = {}

            # Iterate over each column in the DataFrame
            for column_num, column_name in enumerate(df.columns):
                # Iterate over each row in the DataFrame
                for _, row in df.iterrows():
                    # Skip the first column
                    if not column_num:
                        continue
                    # Get the sequence ID from the column name
                    seq_id = column_name
                    # Get the header from the first cell in the row
                    header = row[0]
                    # Get the cell data
                    data = row[column_name]
                    # If the sequence ID is not already in the dictionary,
                    # add it
                    if seq_id not in dataframes[section_name]:
                        dataframes[section_name][seq_id] = {}
                    # Update the dictionary with the header and cell data
                    dataframes[section_name][seq_id].update(
                        {
                            header: data
                        }
                    )
        else:
            # Process other sections
            dataframes[section_name] = df.set_index(
                df.columns[0]
            )[1].to_dict()

    # Return the dictionary of dataframes
    return dataframes


@shared_task
def clean_old_containers():
    """
    Remove containers matching regexes if they are over one week old
    """
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Patterns we have to worry about - data-request-digits, geneseekr-digits
    # TODO: Add more of these as more analysis types get created.
    patterns_to_search = [
        re.compile('^ampliseq.+'),
        re.compile('^amrsummary-\d+-\w+$'),
        re.compile('^cowsnphr.+'),
        re.compile('^data-request-\d+$'),
        re.compile('^geneseekr-\d+-\w+$'),
        re.compile('^mash-\d+-\w+$'),
        re.compile('^neighbor-\d+$'),
        re.compile('^neighbor-\w+-\d+$'),
        re.compile('^parsnp-\d+-\w+$'),
        re.compile('^primer-\w+-\d+$'),
        re.compile('^primer-\w+-\d+-\w+$'),
        re.compile('^prokka-\d+-\w+$'),
        re.compile('^tree-\d+-\w+$'),
    ]
    generator = blob_client.list_containers(include_metadata=True)
    for container in generator:
        for pattern in patterns_to_search:
            if re.match(pattern, container.name):
                today = datetime.now(timezone.utc)
                container_age = abs(
                    container.properties.last_modified - today
                ).days
                if container_age > 7:
                    blob_client.delete_container(container.name)


@shared_task
def clean_old_models():
    """
    Delete models after seven days
    """
    models = [
        AmpliSeqRequest.objects.all(),  # AmpliSeq
        COWSNPhRRequest.objects.all(),  # COWSNPhR
        Regexes.objects.all(),  # FileZone
        AMRSummary.objects.all(),  # GeneSeekr
        GeneSeekrRequest.objects.all(),
        NearestNeighbors.objects.all(),
        ProkkaRequest.objects.all(),
        Tree.objects.all(),
        PrimerVerifierRequest.objects.all(),  # PrimerFinder
        ValidatorRequest.objects.all(),
    ]
    # Find today's date
    today = datetime.today().date()
    # Iterate over all the models in the list
    for model in models:
        # Iterate over each database entry in the list
        for request in model:
            # Calculate the difference between when the database entry was
            # created and today
            request_age = abs(request.created_at - today).days
            # Determine if the difference is over seven days
            if request_age > 7:
                # The request will be None if there are no database entries
                if request is not None:
                    # Delete all database entries created over seven days ago
                    request.delete()


NANOPORE_SUBMISSION_MAX_RETRIES = getattr(
    settings, "NANOPORE_SUBMISSION_MAX_RETRIES", 8
)


NANOPORE_SUBMISSION_RETRY_DELAY = getattr(
    settings, "NANOPORE_SUBMISSION_RETRY_DELAY", 60
)


NANOPORE_SUBMISSION_MAX_RETRY_DELAY = getattr(
    settings, "NANOPORE_SUBMISSION_MAX_RETRY_DELAY", 900
)


def _batch_submission_is_retryable(response, response_data):
    if response_data.get("retryable") is True:
        return True
    if response.status_code == 503 and response_data.get("error_code") == (
        "capacity_unavailable"
    ):
        return True
    return "insufficient capacity" in response_data.get("error", "").lower()


def _nanopore_batch_is_active(run):
    """Return whether one persistent Azure task owns this run."""
    return run.nanopore_processing_batches.filter(
        status__in=("submitting", "submitted", "running"),
        azure_job_id__gt="",
    ).exists()


def _nanopore_collection_blocker(run):
    """Return the oldest generation whose result is not published yet."""
    return (
        run.nanopore_processing_batches.filter(
            status__in=(
                "pending",
                "submitting",
                "submitted",
                "running",
                "published",
            ),
            last_completed_at__isnull=True,
        )
        .order_by("generation", "pk")
        .first()
    )


def _unclaimed_nanopore_files(run):
    """Return uploaded POD5 files that are not assigned to a generation."""
    return NanoporeFile.objects.filter(
        sequencing_run=run,
        status="uploaded",
        processing_batch__isnull=True,
    )


def _schedule_nanopore_collection(run_id, force=False, countdown=0):
    """Schedule collection after the surrounding transaction commits."""
    def schedule():
        collect_nanopore_iteration.apply_async(
            queue="cowbat",
            args=(run_id,),
            kwargs={"force": force},
            countdown=countdown,
        )

    transaction.on_commit(schedule)


def _nanopore_result_payload(blob_service, processing_batch):
    """Return one published generation result, or None when unavailable."""
    if not processing_batch.report_url:
        return None
    container_name = getattr(
        settings,
        "NANOPORE_RESULTS_CONTAINER",
        "nanopore-results",
    )
    exists = blob_service.exists(
        container_name=container_name,
        blob_name=processing_batch.report_url,
    )
    if exists is not True:
        return None
    result = blob_service.get_blob_to_text(
        container_name=container_name,
        blob_name=processing_batch.report_url,
    )
    content = getattr(result, "content", result)
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("Nanopore result must be a JSON object.")
    return payload


def _nanopore_result_identity_error(run, batch_record, payload):
    """Reject stale results before changing database state."""
    try:
        run_id = int(payload.get("run_id"))
        generation = int(payload.get("generation", payload.get("iteration")))
    except (TypeError, ValueError):
        return "Nanopore result has invalid run ID or generation."
    if (run_id != run.pk or payload.get("run_name") != run.run_name
            or generation != batch_record.generation):
        return ("Nanopore result identity mismatch: expected run {0}, "
                "generation {1}; found run {2}, generation {3}.").format(
                    run.pk, batch_record.generation, run_id, generation)
    return None


def _reconcile_nanopore_generation_results(run, blob_service):
    """Persist newly published immutable generation result manifests."""
    completed = []
    failed = []
    batches = run.nanopore_processing_batches.filter(
        status__in=("submitted", "running", "published"),
        last_completed_at__isnull=True,
    ).order_by("generation")
    for batch_record in batches:
        payload = _nanopore_result_payload(blob_service, batch_record)
        if payload is None:
            continue
        identity_error = _nanopore_result_identity_error(run, batch_record, payload)
        if identity_error:
            batch_record.status = "failed"
            batch_record.error = identity_error
            batch_record.save(update_fields=["status", "error"])
            failed.append(batch_record)
            logger.error(identity_error)
            continue
        result_status = str(payload.get("status", "")).strip().lower()
        exit_code = payload.get("scheduler_exit_code")
        try:
            exit_code = int(exit_code)
        except (TypeError, ValueError):
            pass
        # The immutable result manifest describes completion of one
        # scheduler generation, not termination of the persistent Azure
        # wrapper task. Keep finished_at unset until Azure itself reports
        # that the wrapper task reached the Completed state.
        completed_at = django_timezone.now()
        batch_record.report = payload
        batch_record.exit_code = exit_code
        batch_record.last_completed_at = completed_at
        if result_status in (
            "complete",
            "completed",
            "success",
            "succeeded",
        ) and exit_code == 0:
            batch_record.processed_pod5_count = batch_record.pod5_count
            if batch_record.status == "published":
                batch_record.status = "complete"
            completed.append(batch_record)
        else:
            if not batch_record.azure_job_id:
                batch_record.status = "failed"
            batch_record.error = (
                "Nanopore generation {0} reported status {1!r} with "
                "scheduler exit code {2!r}."
            ).format(batch_record.generation, result_status, exit_code)
            failed.append(batch_record)
        batch_record.save(update_fields=[
            "status",
            "report",
            "exit_code",
            "last_completed_at",
            "processed_pod5_count",
            "error",
        ])
    return completed, failed


def _claim_nanopore_batch(run):
    """Claim all uploaded files in one immutable quiet-window generation."""
    files = list(
        NanoporeFile.objects.select_for_update()
        .filter(
            sequencing_run=run,
            status="uploaded",
            processing_batch__isnull=True,
        )
        .order_by("pk")
    )
    if not files:
        return None

    generation = (
        run.nanopore_manifests.order_by("-generation")
        .values_list("generation", flat=True)
        .first()
        or 0
    ) + 1
    manifest_files = [
        {
            "id": nanopore_file.pk,
            "relative_path": nanopore_file.relative_path,
            "blob_name": nanopore_file.blob_name,
            "size_bytes": nanopore_file.size_bytes,
            "sha256": nanopore_file.sha256,
        }
        for nanopore_file in files
    ]
    manifest_json = json.dumps(
        manifest_files,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest = NanoporeManifest.objects.create(
        sequencing_run=run,
        files=manifest_files,
        sha256=hashlib.sha256(manifest_json).hexdigest(),
        generation=generation,
        finalized=True,
    )
    processing_batch = NanoporeProcessingBatch.objects.create(
        sequencing_run=run,
        manifest=manifest,
        generation=generation,
        status="pending",
        pod5_count=len(files),
        report_url=(
            "runs/{0}/iterations/iteration-{1:06d}/manifests/"
            "iteration-{1:06d}.json"
        ).format(run.run_name, generation),
    )
    NanoporeFile.objects.filter(
        pk__in=[nanopore_file.pk for nanopore_file in files]
    ).update(processing_batch=processing_batch)
    return processing_batch


def _write_nanopore_control(run, state):
    """Publish the mutable control state consumed by the Batch wrapper."""
    blob_service = BlockBlobService(
        account_key=settings.AZURE_ACCOUNT_KEY,
        account_name=settings.AZURE_ACCOUNT_NAME,
    )
    blob_service.create_blob_from_text(
        container_name=settings.NANOPORE_RAW_CONTAINER,
        blob_name="{0}/input/control/state.json".format(run.run_name),
        text=json.dumps(
            {
                "schema_version": 1,
                "run_id": run.pk,
                "run_name": run.run_name,
                "state": state,
                "updated_at": django_timezone.now().isoformat(),
            },
            sort_keys=True,
        ),
    )


@shared_task

def collect_nanopore_iteration(sequencing_run_pk, force=False):
    """Claim waiting uploads after the preceding generation is published.

    Files stay uploaded and unclaimed while any earlier generation lacks an
    immutable result manifest. Once that result is published, all accumulated
    files are claimed into one generation. ``force=True`` skips only the quiet
    window; it never bypasses the preceding-generation gate.
    """
    with transaction.atomic():
        try:
            sequencing_run = SequencingRun.objects.select_for_update().get(
                pk=sequencing_run_pk
            )
        except SequencingRun.DoesNotExist:
            return {
                "status": "ignored",
                "reason": "run does not exist",
                "run_id": sequencing_run_pk,
            }
        if sequencing_run.workflow_state in ("complete", "error"):
            return {
                "status": "ignored",
                "reason": "run is closed",
                "run_id": sequencing_run.pk,
            }

        blocker = _nanopore_collection_blocker(sequencing_run)
        if blocker is not None:
            waiting_count = _unclaimed_nanopore_files(sequencing_run).count()
            return {
                "status": "deferred",
                "reason": "previous iteration result is not published",
                "run_id": sequencing_run.pk,
                "active_batch_id": blocker.pk,
                "active_generation": blocker.generation,
                "active_status": blocker.status,
                "waiting_file_count": waiting_count,
            }

        nanopore_run = sequencing_run.nanopore_run
        if not force and nanopore_run.last_file_seen_at is not None:
            collection_window = getattr(
                settings,
                "NANOPORE_COLLECTION_WINDOW_SECONDS",
                60,
            )
            ready_at = nanopore_run.last_file_seen_at + timedelta(
                seconds=collection_window
            )
            now = django_timezone.now()
            if now < ready_at:
                countdown = max(
                    1,
                    int((ready_at - now).total_seconds()) + 1,
                )
                _schedule_nanopore_collection(
                    sequencing_run.pk,
                    force=False,
                    countdown=countdown,
                )
                return {
                    "status": "deferred",
                    "reason": "collection window is still open",
                    "run_id": sequencing_run.pk,
                    "countdown": countdown,
                }

        processing_batch = _claim_nanopore_batch(sequencing_run)
        if processing_batch is None:
            return {
                "status": "idle",
                "reason": "no unclaimed uploaded files",
                "run_id": sequencing_run.pk,
            }

        sequencing_run.workflow_state = (
            "stopping"
            if sequencing_run.workflow_state == "stopping"
            else "processing"
        )
        sequencing_run.status = "Unprocessed"
        sequencing_run.save(update_fields=["workflow_state", "status"])
        nanopore_run.status = (
            "stopping"
            if sequencing_run.workflow_state == "stopping"
            else "processing"
        )
        nanopore_run.save(update_fields=["status"])
        batch_pk = processing_batch.pk
        generation = processing_batch.generation
        pod5_count = processing_batch.pod5_count
        run_id = sequencing_run.pk

        def publish_or_submit():
            submit_nanopore_batch.apply_async(
                queue="cowbat",
                args=(run_id,),
            )

        transaction.on_commit(publish_or_submit)

    logger.info(
        "Collected Nanopore generation run=%s batch=%s generation=%s "
        "pod5_count=%s force=%s",
        sequencing_run_pk,
        batch_pk,
        generation,
        pod5_count,
        force,
    )
    return {
        "status": "queued",
        "run_id": sequencing_run_pk,
        "batch_id": batch_pk,
        "generation": generation,
        "pod5_count": pod5_count,
    }


def _scheduler_csvs(sequencing_run, nanopore_run):
    barcode_values = nanopore_run.barcode_values or [
        int(value.strip())
        for value in settings.NANOPORE_BARCODE_VALUES.split(",")
        if value.strip()
    ]
    samples = (nanopore_run.sample_metadata or {}).get("samples") or [
        {
            "barcode": value,
            "seqid": "barcode{0:02d}".format(value),
            "olnid": "BARCODE-{0:02d}".format(value),
        }
        for value in barcode_values
    ]
    nanopore_run.barcode_kit = (
        nanopore_run.barcode_kit or settings.NANOPORE_BARCODE_KIT
    )
    nanopore_run.barcode_values = barcode_values
    nanopore_run.sample_metadata = {"samples": samples}
    input_csv = (
        "reference,pod5_dir,output_dir,barcode,barcode_values,run_id\n"
        "{0},$AZ_BATCH_TASK_WORKING_DIR/source,"
        "$AZ_BATCH_TASK_WORKING_DIR/output,{1},{2},{3}\n"
    ).format(
        settings.NANOPORE_REFERENCE,
        nanopore_run.barcode_kit,
        ",".join(str(value) for value in barcode_values),
        sequencing_run.run_name,
    )
    metadata_csv = "Barcode,SEQID,OLNID\n" + "".join(
        "{0},{1},{2}\n".format(
            sample["barcode"], sample["seqid"], sample.get("olnid", "")
        )
        for sample in sorted(samples, key=lambda item: item["barcode"])
    )
    return input_csv, metadata_csv


@shared_task(bind=True, max_retries=NANOPORE_SUBMISSION_MAX_RETRIES)
def submit_nanopore_batch(self, sequencing_run_pk):
    with transaction.atomic():
        sequencing_run = SequencingRun.objects.select_for_update().get(
            pk=sequencing_run_pk
        )
        publish_only = _nanopore_batch_is_active(sequencing_run)
        processing_batch = (
            sequencing_run.nanopore_processing_batches.filter(status="pending")
            .order_by("generation")
            .first()
        )
        if processing_batch is None:
            return
        if not publish_only:
            if processing_batch.status == "submitted" or processing_batch.azure_job_id:
                return
            if processing_batch.status == "submitting" and self.request.retries == 0:
                return
        if sequencing_run.workflow_state in ("complete", "error"):
            return
        if not settings.NANOPORE_BATCH_COMMAND:
            processing_batch.status = "failed"
            processing_batch.error = "NANOPORE_BATCH_COMMAND is not configured."
            processing_batch.save(update_fields=["status", "error"])
            sequencing_run.workflow_state = "error"
            sequencing_run.save(update_fields=["workflow_state"])
            return
        if not publish_only:
            processing_batch.status = "submitting"
            processing_batch.error = ""
            processing_batch.save(update_fields=["status", "error"])

    manifest = processing_batch.manifest
    storage_prefix = sequencing_run.run_name
    blob_service = BlockBlobService(
        account_key=settings.AZURE_ACCOUNT_KEY, account_name=settings.AZURE_ACCOUNT_NAME
    )
    manifest_prefix = "{0}/input/manifests/input-manifest-v".format(storage_prefix)
    manifest_blob_name = "{0}{1:06d}.json".format(
        manifest_prefix, processing_batch.generation
    )
    control_blob_name = "{0}/input/control/state.json".format(storage_prefix)
    nanopore_run = sequencing_run.nanopore_run
    if not nanopore_run.scheduler_input_csv:
        input_csv, metadata_csv = _scheduler_csvs(sequencing_run, nanopore_run)
        nanopore_run.scheduler_input_csv = input_csv
        nanopore_run.scheduler_metadata_csv = metadata_csv
        nanopore_run.scheduler_input_sha256 = hashlib.sha256(
            input_csv.encode("utf-8")
        ).hexdigest()
        nanopore_run.scheduler_metadata_sha256 = hashlib.sha256(
            metadata_csv.encode("utf-8")
        ).hexdigest()
        nanopore_run.save(
            update_fields=[
                "scheduler_input_csv",
                "scheduler_metadata_csv",
                "scheduler_input_sha256",
                "scheduler_metadata_sha256",
            ]
        )
    barcode_values = nanopore_run.barcode_values
    manifest_payload = {
        "schema_version": 1,
        "run_id": sequencing_run.pk,
        "run_name": sequencing_run.run_name,
        "finalized": manifest.finalized,
        "finalized_at": manifest.created_at.isoformat(),
        "dorado": {
            "model": nanopore_run.dorado_model or settings.NANOPORE_DORADO_MODEL,
            "barcode_kit": nanopore_run.barcode_kit,
        },
        "reference": {"path": settings.NANOPORE_REFERENCE},
        "scheduler": {
            "input_blob": "{0}/input/input.csv".format(storage_prefix),
            "metadata_blob": "{0}/input/metadata.csv".format(storage_prefix),
            "input_sha256": nanopore_run.scheduler_input_sha256,
            "metadata_sha256": nanopore_run.scheduler_metadata_sha256,
        },
        "barcodes": [
            {
                "barcode": value,
                "seqid": next(
                    sample["seqid"]
                    for sample in nanopore_run.sample_metadata["samples"]
                    if sample["barcode"] == value
                ),
                "olnid": next(
                    sample.get("olnid", "")
                    for sample in nanopore_run.sample_metadata["samples"]
                    if sample["barcode"] == value
                ),
            }
            for value in barcode_values
        ],
        "files": manifest.files,
    }
    manifest_text = json.dumps(manifest_payload, sort_keys=True)
    logger.info(
        "Publishing Nanopore input manifest run=%s batch=%s generation=%s "
        "status=%s publish_only=%s blob=%s",
        sequencing_run.pk,
        processing_batch.pk,
        processing_batch.generation,
        processing_batch.status,
        publish_only,
        manifest_blob_name,
    )
    try:
        blob_service.create_blob_from_text(
            container_name=settings.NANOPORE_RAW_CONTAINER,
            blob_name=manifest_blob_name,
            text=manifest_text,
        )
        blob_service.create_blob_from_text(
            container_name=settings.NANOPORE_RAW_CONTAINER,
            blob_name="{0}/input/input.csv".format(storage_prefix),
            text=nanopore_run.scheduler_input_csv,
        )
        blob_service.create_blob_from_text(
            container_name=settings.NANOPORE_RAW_CONTAINER,
            blob_name="{0}/input/metadata.csv".format(storage_prefix),
            text=nanopore_run.scheduler_metadata_csv,
        )
        # Do not publish the stopping control state here. A finalized run
        # may still have this newly published generation to process. The
        # monitor writes stopping only after every generation result has been
        # reconciled and no uploaded files remain unclaimed.
        if publish_only:
            processing_batch.status = "published"
            processing_batch.submitted_at = (
                processing_batch.submitted_at or django_timezone.now()
            )
            processing_batch.save(update_fields=["status", "submitted_at"])
            logger.info(
                "Published Nanopore manifest run=%s generation=%s blob=%s",
                sequencing_run.pk,
                processing_batch.generation,
                manifest_blob_name,
            )
            return
        mount_root = "$AZ_BATCH_NODE_MOUNTS_DIR/{0}".format(
            settings.NANOPORE_RAW_CONTAINER
        )
        command = build_nanopore_command(
            settings.NANOPORE_BATCH_COMMAND,
            run_id=sequencing_run.pk,
            run_name=sequencing_run.run_name,
            manifest_blob=manifest_blob_name,
            input_container=settings.NANOPORE_RAW_CONTAINER,
            output_container=settings.NANOPORE_RESULTS_CONTAINER,
            source_root="$AZ_BATCH_TASK_WORKING_DIR/source",
            task_root="$AZ_BATCH_TASK_WORKING_DIR",
            manifest_prefix=manifest_prefix,
            control_blob=control_blob_name,
        )
        request_data = {
            "container": settings.NANOPORE_RAW_CONTAINER,
            "command_file": command,
            "vm_size": settings.NANOPORE_BATCH_VM_SIZE,
            "input_file_pattern": [
                "{0}/{1}".format(settings.NANOPORE_RAW_CONTAINER, manifest_blob_name),
                "{0}/{1}/input/pod5/*".format(
                    settings.NANOPORE_RAW_CONTAINER, storage_prefix
                ),
                "{0}/{1}/input/input.csv".format(
                    settings.NANOPORE_RAW_CONTAINER, storage_prefix
                ),
                "{0}/{1}/input/metadata.csv".format(
                    settings.NANOPORE_RAW_CONTAINER, storage_prefix
                ),
            ],
            "download_file_pattern": [],
            "output_file_pattern": ["output/"],
            "output_container": settings.NANOPORE_RESULTS_CONTAINER,
            "output_prefix": "runs/{0}".format(sequencing_run.run_name),
            "analysis_type": "Nanopore",
            "unique_id": "foodport-nanopore-{0}".format(sequencing_run.pk),
            "log_prefix": "runs/{0}/logs".format(sequencing_run.run_name),
        }
        response = requests.post(
            settings.NANOPORE_BATCH_SERVICE_URL,
            headers=settings.BATCH_URL_HEADERS,
            data=json.dumps(request_data),
            timeout=10,
        )
        response_data = response.json()
        response_status = getattr(response, "status_code", 200)
        if isinstance(response_status, int) and response_status >= 400:
            if _batch_submission_is_retryable(response, response_data):
                raise RuntimeError(
                    "Retryable Batch capacity error: {0}".format(
                        response_data.get("error", "capacity unavailable")
                    )
                )
            response.raise_for_status()
        response.raise_for_status()
        submission_status = str(
            response_data.get("status", "")
        ).strip().lower()
        if submission_status not in ("success", "submitted"):
            raise RuntimeError(
                "Batch service submission failed: {0}".format(
                    response_data.get("error", "Unknown Batch service error")
                )
            )
        job_id = response_data["job_id"]
        task_ids = response_data.get("tasks", [])
        if not task_ids:
            raise ValueError("Batch service returned no task ID.")
    except Exception as exc:
        response_data = locals().get("response_data", {})
        retryable = (
            "Retryable Batch capacity error" in str(exc)
            or response_data.get("retryable") is True
        )
        if retryable and self.request.retries < self.max_retries:
            retry_number = self.request.retries + 1
            processing_batch.status = "submitting"
            processing_batch.error = (
                "Azure GPU capacity is temporarily unavailable. "
                "Retrying submission (attempt {0} of {1})."
            ).format(retry_number, self.max_retries)
            processing_batch.save(update_fields=["status", "error"])
            sequencing_run.task_retry_count = retry_number
            sequencing_run.save(update_fields=["task_retry_count"])
            logger.warning(
                "Nanopore run %s hit transient Azure capacity on attempt "
                "%s/%s; retrying",
                sequencing_run.pk,
                retry_number,
                self.max_retries,
                exc_info=True,
            )
            raise self.retry(
                exc=exc,
                countdown=min(
                    NANOPORE_SUBMISSION_MAX_RETRY_DELAY,
                    NANOPORE_SUBMISSION_RETRY_DELAY * (2**self.request.retries),
                ),
            )
        processing_batch.status = "failed"
        if retryable:
            processing_batch.error = (
                "Azure GPU capacity remained unavailable after {0} retries."
            ).format(self.max_retries)
        else:
            processing_batch.error = str(exc)[:1000]
        processing_batch.save(update_fields=["status", "error"])
        sequencing_run.workflow_state = "error"
        sequencing_run.save(update_fields=["workflow_state"])
        sequencing_run.nanopore_run.status = "error"
        sequencing_run.nanopore_run.save(update_fields=["status"])
        return

    processing_batch.status = "submitted"
    processing_batch.azure_pool_id = str(response_data.get("pool_id", ""))
    processing_batch.azure_job_id = str(job_id)
    processing_batch.azure_task_id = str(task_ids[0])
    processing_batch.submitted_at = django_timezone.now()
    processing_batch.save(
        update_fields=[
            "status",
            "azure_pool_id",
            "azure_job_id",
            "azure_task_id",
            "submitted_at",
        ]
    )
    sequencing_run.workflow_state = (
        "stopping" if sequencing_run.workflow_state == "stopping" else "processing"
    )
    sequencing_run.job_id = str(job_id)
    sequencing_run.task_id = str(task_ids[0])
    sequencing_run.batch_submit_status = response_data.get("status", "")
    sequencing_run.save(
        update_fields=["workflow_state", "job_id", "task_id", "batch_submit_status"]
    )


def build_nanopore_command(template, **paths):
    """Format the image-installed wrapper command with mounted task paths."""
    command = template.format(**paths)
    if paths.get("manifest_prefix") and "--manifest-prefix" not in command:
        command += ' --manifest-prefix "{0}"'.format(paths["manifest_prefix"])
    if paths.get("control_blob") and "--control-blob" not in command:
        command += ' --control-blob "{0}"'.format(paths["control_blob"])
    return command


def _batch_resource_missing(exception):
    """Return whether an Azure Batch exception means a resource is absent."""
    error = getattr(exception, "error", None)
    code = getattr(error, "code", None)
    if hasattr(code, "value"):
        code = code.value
    code = str(code or "").strip().lower()

    response = getattr(exception, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(exception, "status_code", None)

    message = str(exception).lower()
    missing_codes = (
        "jobnotfound",
        "poolnotfound",
        "resourcenotfound",
        "notfound",
    )
    missing_messages = (
        "does not exist",
        "could not be found",
        "resource not found",
        "the specified job does not exist",
        "the specified pool does not exist",
    )
    return (
        status_code == 404
        or code in missing_codes
        or any(value in message for value in missing_messages)
    )


def _batch_state_value(resource):
    """Return a normalized Azure Batch resource state string."""
    state = getattr(resource, "state", None)
    if hasattr(state, "value"):
        state = state.value
    state = str(state or "").strip().lower()
    if "." in state:
        state = state.rsplit(".", 1)[-1]
    return state.replace("_", "").replace("-", "")


def _append_unique_run_errors(run, errors):
    """Append cleanup errors without duplicating messages every poll cycle."""
    current_errors = list(run.errors or [])
    changed = False
    for error in errors:
        if error not in current_errors:
            current_errors.append(error)
            changed = True
    if changed:
        run.errors = current_errors
    return changed


def _cleanup_nanopore_batch_resources(*, batch_client, processing_batch, run):
    """Request and verify asynchronous Nanopore job and pool deletion.

    Azure Batch resource deletion is asynchronous. The job is deleted first.
    A later monitor cycle verifies that the job is absent before requesting
    pool deletion. Azure identifiers are cleared only after resource absence
    is confirmed, allowing cleanup to resume after worker restarts.
    """
    resource_errors = []
    updated_fields = []

    job_id = processing_batch.azure_job_id
    if job_id:
        try:
            try:
                job = batch_client.job.get(job_id=job_id)
            except BatchErrorException as exc:
                if not _batch_resource_missing(exc):
                    raise
                job = None

            if job is None:
                logger.info(
                    "Confirmed Nanopore Batch job deletion run=%s job=%s",
                    run.pk,
                    job_id,
                )
                processing_batch.azure_job_id = ""
                processing_batch.azure_task_id = ""
                updated_fields.extend(["azure_job_id", "azure_task_id"])
            else:
                job_state = _batch_state_value(job)
                if job_state != "deleting":
                    logger.warning(
                        "Requesting Nanopore Batch job deletion run=%s job=%s",
                        run.pk,
                        job_id,
                    )
                    batch_client.job.delete(job_id=job_id)
                else:
                    logger.info(
                        "Nanopore Batch job deletion is pending run=%s job=%s",
                        run.pk,
                        job_id,
                    )
        except Exception as exc:
            message = "Nanopore job cleanup error: {0}".format(exc)
            resource_errors.append(message)
            logger.exception(
                "Could not clean up Nanopore Batch job run=%s job=%s",
                run.pk,
                job_id,
            )

        if updated_fields:
            processing_batch.save(update_fields=updated_fields)

        if resource_errors and _append_unique_run_errors(run, resource_errors):
            run.save(update_fields=["errors"])

        # The pool must not be deleted until Azure confirms job deletion.
        if processing_batch.azure_job_id:
            return {
                "complete": False,
                "pending": True,
                "errors": resource_errors,
            }

    pool_id = processing_batch.azure_pool_id
    if pool_id:
        try:
            try:
                pool = batch_client.pool.get(pool_id=pool_id)
            except BatchErrorException as exc:
                if not _batch_resource_missing(exc):
                    raise
                pool = None

            if pool is None:
                logger.info(
                    "Confirmed Nanopore Batch pool deletion run=%s pool=%s",
                    run.pk,
                    pool_id,
                )
                processing_batch.azure_pool_id = ""
                if "azure_pool_id" not in updated_fields:
                    updated_fields.append("azure_pool_id")
            else:
                pool_state = _batch_state_value(pool)
                if pool_state != "deleting":
                    logger.warning(
                        "Requesting Nanopore Batch pool deletion run=%s pool=%s",
                        run.pk,
                        pool_id,
                    )
                    batch_client.pool.delete(pool_id=pool_id)
                else:
                    logger.info(
                        "Nanopore Batch pool deletion is pending run=%s pool=%s",
                        run.pk,
                        pool_id,
                    )
        except Exception as exc:
            message = "Nanopore pool cleanup error: {0}".format(exc)
            resource_errors.append(message)
            logger.exception(
                "Could not clean up Nanopore Batch pool run=%s pool=%s",
                run.pk,
                pool_id,
            )

    if updated_fields:
        processing_batch.save(update_fields=updated_fields)

    if resource_errors and _append_unique_run_errors(run, resource_errors):
        run.save(update_fields=["errors"])

    complete = not (
        processing_batch.azure_job_id
        or processing_batch.azure_pool_id
    )
    return {
        "complete": complete,
        "pending": not complete,
        "errors": resource_errors,
    }


def check_nanopore_tasks():
    """Reconcile generation publication, task state, and resource cleanup."""
    owner_batches = (
        NanoporeProcessingBatch.objects.filter(
            Q(azure_job_id__gt="") | Q(azure_pool_id__gt="")
        )
        .select_related("sequencing_run", "sequencing_run__nanopore_run")
        .order_by("pk")
    )
    if not owner_batches.exists():
        return

    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY,
    )
    batch_client = batch.BatchServiceClient(
        credentials,
        base_url=settings.BATCH_ACCOUNT_URL,
    )
    result_blobs = BlockBlobService(
        account_key=settings.AZURE_ACCOUNT_KEY,
        account_name=settings.AZURE_ACCOUNT_NAME,
    )

    for owner_batch in owner_batches:
        run = owner_batch.sequencing_run
        try:
            completed_generations, failed_generations = (
                _reconcile_nanopore_generation_results(run, result_blobs)
            )
            if failed_generations:
                failed_batch = failed_generations[0]
                run.workflow_state = "error"
                run.status = "Error"
                run.failure_stage = "processing"
                run.nanopore_run.status = "error"
                run.exit_code = failed_batch.exit_code
                _append_unique_run_errors(run, [failed_batch.error])
                run.nanopore_run.save(update_fields=["status"])
                run.save(update_fields=[
                    "workflow_state", "status", "failure_stage", "exit_code",
                    "errors",
                ])
                _write_nanopore_control(run, "error")

            waiting_count = _unclaimed_nanopore_files(run).count()
            blocker = _nanopore_collection_blocker(run)
            if (run.workflow_state != "error" and not failed_generations
                    and waiting_count > 0 and blocker is None):
                collect_nanopore_iteration.apply_async(
                    queue="cowbat",
                    args=(run.pk,),
                    kwargs={"force": run.workflow_state == "stopping"},
                )
            elif (
                not failed_generations
                and run.workflow_state == "stopping"
                and waiting_count == 0
                and blocker is None
            ):
                _write_nanopore_control(run, "stopping")

            # Save the terminal observation before deletion starts. Later
            # monitor cycles then bypass task polling and resume cleanup.
            if owner_batch.finished_at is None:
                try:
                    cloud_tasks = list(batch_client.task.list(
                        owner_batch.azure_job_id
                    ))
                except BatchErrorException as exc:
                    if not _batch_resource_missing(exc):
                        raise
                    owner_batch.exit_code = None
                    owner_batch.finished_at = django_timezone.now()
                    owner_batch.error = (
                        "Azure Batch job disappeared before FoodPort observed "
                        "a terminal Nanopore task state."
                    )
                    owner_batch.save(update_fields=[
                        "exit_code", "finished_at", "error",
                    ])
                    run.workflow_state = "error"
                    run.status = "Error"
                    run.failure_stage = "cleanup"
                    run.nanopore_run.status = "error"
                    _append_unique_run_errors(run, [owner_batch.error])
                    run.nanopore_run.save(update_fields=["status"])
                    run.save(update_fields=[
                        "workflow_state", "status", "failure_stage", "errors",
                    ])
                    cloud_tasks = []

                if owner_batch.finished_at is None:
                    cloud_task = next(
                        (
                            task for task in cloud_tasks
                            if str(getattr(task, "id", ""))
                            == owner_batch.azure_task_id
                        ),
                        cloud_tasks[0] if cloud_tasks else None,
                    )
                    if cloud_task is None:
                        continue
                    if cloud_task.state != batchmodels.TaskState.completed:
                        if run.workflow_state == "error":
                            continue
                        owner_batch.status = "running"
                        owner_batch.started_at = (
                            owner_batch.started_at or django_timezone.now()
                        )
                        if run.workflow_state not in ("stopping", "error"):
                            run.workflow_state = "processing"
                        run.task_started_at = (
                            run.task_started_at or django_timezone.now()
                        )
                        owner_batch.save(update_fields=["status", "started_at"])
                        run.save(update_fields=[
                            "workflow_state", "task_started_at",
                        ])
                        continue

                    # Read the persistent wrapper task exit code directly
                    # from Azure. owner_batch.exit_code may currently hold the
                    # scheduler exit code from the latest generation result,
                    # which must never be used as proof that the wrapper ended.
                    wrapper_exit_code = getattr(
                        getattr(cloud_task, "execution_info", None),
                        "exit_code", None,
                    )
                    owner_batch.exit_code = wrapper_exit_code
                    owner_batch.finished_at = django_timezone.now()
                    run.exit_code = wrapper_exit_code
                    run.task_finished_at = owner_batch.finished_at
                    owner_batch.save(update_fields=["exit_code", "finished_at"])
                    run.save(update_fields=["exit_code", "task_finished_at"])

            wrapper_exit_code = owner_batch.exit_code
            scheduler_exit_code = None
            if isinstance(owner_batch.report, dict):
                scheduler_exit_code = owner_batch.report.get(
                    "scheduler_exit_code"
                )
            # A successful wrapper cannot substitute for missing results.
            unresolved = run.nanopore_processing_batches.filter(
                last_completed_at__isnull=True,
            )

            waiting_count = _unclaimed_nanopore_files(run).count()
            blocker = _nanopore_collection_blocker(run)
            successful_drain = (
                wrapper_exit_code == 0
                and run.workflow_state != "error"
                and waiting_count == 0
                and blocker is None
                and not unresolved.exists()
                and not run.nanopore_processing_batches.filter(status="failed").exists()
            )

            if not successful_drain:
                owner_batch.status = "failed"
                if not owner_batch.error:
                    owner_batch.error = (
                        "Azure Batch wrapper task exited with code {0} "
                        "before all Nanopore generations were drained. "
                        "The latest reconciled scheduler generation exited "
                        "with code {1}."
                    ).format(wrapper_exit_code, scheduler_exit_code)
                    if wrapper_exit_code == 0 and unresolved.exists():
                        owner_batch.error = (
                            "Azure Batch wrapper exited without all generation "
                            "result manifests."
                        )
                run.workflow_state = "error"
                run.status = "Error"
                run.failure_stage = "processing"
                run.nanopore_run.status = "error"
                _append_unique_run_errors(run, [owner_batch.error])
                owner_batch.save(update_fields=[
                    "status", "last_completed_at",
                    "processed_pod5_count", "error",
                ])
                run.nanopore_run.save(update_fields=["status"])
                run.save(update_fields=[
                    "workflow_state", "status", "failure_stage", "errors",
                ])

            cleanup = _cleanup_nanopore_batch_resources(
                batch_client=batch_client,
                processing_batch=owner_batch,
                run=run,
            )
            if not cleanup["complete"]:
                if successful_drain:
                    owner_batch.status = "running"
                    run.workflow_state = "stopping"
                    run.nanopore_run.status = "stopping"
                    owner_batch.save(update_fields=[
                        "status", "last_completed_at", "processed_pod5_count",
                    ])
                    run.nanopore_run.save(update_fields=["status"])
                    run.save(update_fields=["workflow_state"])
                continue

            if successful_drain:
                owner_batch.status = "complete"
                run.workflow_state = "complete"
                run.status = "Processed"
                run.failure_stage = ""
                run.nanopore_run.status = "complete"
                run.nanopore_run.terminated_at = django_timezone.now()
                owner_batch.save(update_fields=[
                    "status", "last_completed_at", "processed_pod5_count",
                    "azure_pool_id", "azure_job_id", "azure_task_id",
                ])
                run.nanopore_run.save(update_fields=["status", "terminated_at"])
                run.save(update_fields=[
                    "workflow_state", "status", "failure_stage",
                ])
            else:
                owner_batch.save(update_fields=[
                    "azure_pool_id", "azure_job_id", "azure_task_id",
                ])

        except BatchErrorException as exc:
            owner_batch.error = str(exc)[:1000]
            owner_batch.save(update_fields=["error"])
            message = "Nanopore Batch polling error: {0}".format(exc)
            if _append_unique_run_errors(run, [message]):
                run.save(update_fields=["errors"])
