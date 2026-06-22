# Standard imports
import datetime
import fnmatch
import logging
import os
import pandas as pd
import re
import shutil
from time import sleep


# Django imports
from django.conf import settings
from django.db import DatabaseError
from django.utils.translation import gettext_lazy as _
# Azure imports
import azure.batch.batch_auth as batch_auth
try:
    import azure.batch as batch
except ImportError:
    import azure.batch.batch_service_client as batch

import azure.batch.models as batchmodels
from azure.common import AzureMissingResourceHttpError, AzureHttpError
try:
    from azure.storage.blob import BlobServiceClient as BlockBlobService
except ImportError:
    from azure.storage.blob import BlockBlobService

try:
    from azure.storage.blob import BlobSasPermissions as BlobPermissions
except ImportError:
    from azure.storage.blob import BlobPermissions

# Celery Task Management
from celery import shared_task

# Sentry
from sentry_sdk import capture_exception

# Portal-specific imports
from olc_webportalv2.common.methods import generic_api_submit
from olc_webportalv2.cowsnphr.models import (
    COWSNPhRAzureTask,
    COWSNPhRRequest,
    ContainerName
)

from olc_webportalv2.primer_finder.methods import send_email
from olc_webportalv2.filezone.methods import (
    FileLocate
)
# Set the logging levels
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def find_containers():
    """
    Find all containers with names that match either a sequencing run, or have
    "cowsnphr".
    Ignore any output containers
    """
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    containers = blob_client.list_containers()
    # Compile the regular expressions
    seq_run_re = re.compile('\d{6}')
    cowsnphr_re = re.compile('cowsnphr')
    # Use a list comprehension to find the matching container names
    run_set = {
        container.name for container in containers
        if (
            (seq_run_re.match(container.name)
             or cowsnphr_re.match(container.name))
            and '-output' not in container.name)
    }
    return sorted(run_set)


@shared_task
def refresh_container_names():
    """
    Update the container names in the model.
    """
    try:
        # Find all the containers
        run_list = set(find_containers())
        # Get the current container names in the database
        current_names = set(
            ContainerName.objects.values_list(
                'container_name',
                flat=True
            )
        )
        # Find the new and deleted container names
        new_names = run_list - current_names
        deleted_names = current_names - run_list
        # Create new container names
        ContainerName.objects.bulk_create(
            ContainerName(container_name=name) for name in new_names
        )
        # Delete removed container names
        ContainerName.objects.filter(container_name__in=deleted_names).delete()
    except (
            AzureMissingResourceHttpError,
            AzureHttpError,
            DatabaseError) as exc:
        capture_exception(exc)


def container_sanity(container_name):
    """
    Ensure that the supplied container has a 'fastq' folder with FASTQ files,
    a 'ref' folder with a FASTA-formatted reference genome, and, optionally, a
    mask file
    """
    # Create a client for listing blobs in the container
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # List all the blobs in the container
    blobs = blob_client.list_blobs(container_name=container_name)
    # Create variables to store whether the query files, the reference genome,
    # and the optional mask file are present
    fastq = False
    ref = False
    mask = str()
    # Check for the query FASTQ files
    for blob in blobs:
        if fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'fastq',
                '*.fastq.gz'
            )
        ):
            fastq = True
    # Check for the FASTA-formatted reference file
    for blob in blobs:
        if fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'ref',
                '*.fasta'
            )
        ):
            ref = True
    # Check for the optional mask file
    for blob in blobs:
        if fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'ref',
                '*.bed'
            )
        ):
            mask = os.path.join(
                'ref',
                os.path.basename(blob.name)
            )
    return fastq, ref, mask


def upload_files_for_cowsnphr(
    file: str,
        container_name: str):
    """
    Upload files for COWSNPhR analyses. Place .fastq.gz files in the "fastq"
    folder, and all other files in the "ref" folder
    """
    # Create a blob client
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Create the container (if required)
    blob_client.create_container(container_name)
    # Determine if the file extension is .gz and set the destination folder
    # appropriately
    if os.path.splitext(file.name)[-1] == '.gz':
        folder = 'fastq'
    else:
        folder = 'ref'
    # Create a variable to store the blob file name and path
    blob_name = os.path.join(folder, file.name)
    # Upload the file to blob
    blob_client.create_blob_from_bytes(
        container_name=container_name,
        blob_name=blob_name,
        blob=file.read()
    )


def create_system_call(
    container_name: str,
        mask: str):
    """
    Create the COWSNPhR system call to be performed on the batch VM
    source $CONDA/activate && cowsnphr -s fastq -r ref -g
    :param str container name: Name of container in which files are stored
    :param str mask: String of path of mask file
    """
    # Copy the files to /datadrive, perform the analyses
    # there, and move everything back once the pipeline is complete

    # Create a variable to store the extremely long path information
    path = '$AZ_BATCH_NODE_MOUNTS_DIR/{container}'.format(
        container=container_name
    )

    # Define the path variables
    fastq_path = os.path.join(
        '/datadrive',
        container_name,
        'fastq'
    )
    ref_path = os.path.join(
        '/datadrive',
        container_name,
        'ref'
    )

    # Create the system call. Activate the conda environment, create the
    # datadrive folder, copy the files to it, and run the analyses. Use the -g
    # flag to enable --gpus and the -w flag to specify the working directory.
    # Then copy everything back to the container
    cmd = (
        'source $CONDA/activate /envs/cowsnphr && '
        'mkdir -p /datadrive/{container_name} && '
        'cp -R {path} /datadrive/ && '
        'cowsnphr -s {fastq_path} '
        '-r {ref_path} -g -w /datadrive'.format(
            container_name=container_name,
            fastq_path=fastq_path,
            path=path,
            ref_path=ref_path
        )
    )

    # Update the command if the mask file was provided
    if mask:
        cmd += ' -m /datadrive/{container}/{mask}'.format(
            container=container_name,
            mask=mask
        )

    # Update the command with the final steps to copy the files back from the
    # datadrive to the container
    cmd += (
        ' && cp -R /datadrive/{container} '
        '$AZ_BATCH_NODE_MOUNTS_DIR'.format(
            container=container_name
        )
    )
    return cmd


@shared_task
def run_cowsnphr_batch(
        primary_key: int):
    """
    Run the necessary functions to submit the COWSNPhR request to batch
    :param int primary_key: Primary key of the COWSNPhRRequest being processed
    """
    cowsnphr = COWSNPhRRequest.objects.get(pk=primary_key)
    # If a list of SEQIDs has been provided, locate and copy the necessary
    # files to the analysis-specific blob container
    if cowsnphr.seqids:
        ref = find_ref(cowsnphr=cowsnphr)
        queries = find_query(cowsnphr=cowsnphr)
        # Do not continue if there were errors locating files
        if cowsnphr.seqid_errors:
            return
        # Copy the blobs to the destination container
        copy_blobs(
                blob_metadata_list=ref,
                destination_container=cowsnphr.container_name,
                destination_path='ref'
            )
        copy_blobs(
                blob_metadata_list=queries,
                destination_container=cowsnphr.container_name,
                destination_path='fastq'
            )
    # Ensure that the necessary files are present
    fastq, ref, mask = container_sanity(
        container_name=cowsnphr.container_name
    )
    if not fastq or not ref:
        if not fastq:
            cowsnphr.error_list.append(
                'FASTQ files could not be located in the "fastq" folder of '
                'the supplied container: {container_name}'.format(
                    container_name=cowsnphr.container_name
                )
            )
        if not ref:
            cowsnphr.error_list.append(
                'A FASTQ-formatted genome could not be located in the "ref" '
                'folder of the supplied container: {container_name}'.format(
                    container_name=cowsnphr.container_name
                )
            )
        cowsnphr.status = 'Error'
        cowsnphr.save()
        return
    # Create the system call
    cmd = create_system_call(
        mask=mask,
        container_name=cowsnphr.container_name
    )

    # Create a folder into which any error code files are to be written
    local_folder = os.path.join('olc_webportalv2', 'media', str(cowsnphr))
    os.makedirs(local_folder, exist_ok=True)

    try:
        # Submit the command to the AzureBatch service
        generic_api_submit(
            command=cmd,
            container_name=cowsnphr.container_name,
            vm_size='Standard_NV18ads_A10_v5',
            analysis_type='COWSNPhR',
            unique_id='FoodPort'
            )

        # Create the AzureTask database entry
        COWSNPhRAzureTask.objects.create(
            cowsnphr=cowsnphr,
            exit_code_file=os.path.join(local_folder, 'exit_codes.txt')
        )
    except Exception as exc:
        file_handler = logging.FileHandler(os.path.join(local_folder, 'error'))
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        logger.exception(exc)
        capture_exception(exc)
        cowsnphr.status = 'Error'
        cowsnphr.error_list.append(exc)
        cowsnphr.save()


def find_ref(cowsnphr: COWSNPhRRequest):
    """
    Use the FileLocate class from FileZone to locate the reference genome
    :param COWSNPhRRequest cowsnphr: COWSNPhRRequest of the the current
    analysis
    """
    # Attempt to locate the reference file in the "processed-data" container
    # first
    file_obj = FileLocate(
        container_regex=['processed-data'],
        container_exclude_regex=[],
        file_regex=['{seqid}.fasta'.format(seqid=cowsnphr.ref)],
        file_exclude_regex=[],
        debug=True
    )
    file_matches, __ = file_obj.main()
    if file_matches:
        ref = file_matches['processed-data']
        return ref
    # Search all containers if a reference was not found in "processed-data"
    file_obj = FileLocate(
        container_regex=['*'],
        container_exclude_regex=[],
        file_regex=['{seqid}.fasta'.format(seqid=cowsnphr.ref)],
        file_exclude_regex=[],
        debug=True
    )
    file_matches, __ = file_obj.main()
    # Update the database entry with an error that the reference file SEQID
    # could not be located
    if not file_matches:
        cowsnphr.seqid_errors.append(
            _('Could not locate a FASTA file for the supplied reference '
              'genome {ref}'
              .format(ref=cowsnphr.ref))
        )
        cowsnphr.status = 'Error'
        cowsnphr.save()
        return None
    # Try to use a reference in the "BestAssemblies" folder
    for __, ref_list in file_matches.items():
        for ref_dict in ref_list:
            if "BestAssemblies" in ref_dict['blob_name']:
                return [ref_dict]
    # If there are no references in a "BestAssemblies" folder, select the
    # first item
    container = next(iter(file_matches))
    ref = file_matches[container]
    return ref


def find_query(cowsnphr: COWSNPhRRequest):
    """
    Use the FileLocate class from FileZone to locate the query genomes
    :param COWSNPhRRequest cowsnphr: COWSNPhRRequest of the the current
        analysis
    """
    # Attempt to locate the query files
    query_list = [
        '{seqid}*.fastq.gz'.format(seqid=seqid) for seqid in cowsnphr.seqids]
    file_obj = FileLocate(
        container_regex=['*'],
        container_exclude_regex=[
            'output', 'ampliseq', 'amrsummary', 'cowbat', 'geneseekr',
            'primer', 'mash', 'neighbor', 'tree', 'vir-typer', 'fake'
            ],
        file_regex=query_list,
        file_exclude_regex=['trimmed', 'baited'],
        debug=True
    )
    file_matches, __ = file_obj.main()
    # If no files were returned, update the database with the error
    if not file_matches:
        cowsnphr.seqid_errors.append(
            _('Could not locate .fastq.gz files for the supplied query '
              'genomes {seqids}'
              .format(seqids=','.join(cowsnphr.seqids)))
        )
        cowsnphr.status = 'Error'
        cowsnphr.save()
        return None
    # Create a lsit to store all the .fastq.gz file matches
    seqids = []
    # Initialise a dictionary to track the presence/absence of forward and
    # reverse reads
    presence_dict = {}
    # Add the forward and reverse reads presence for each SEQID
    for seqid in cowsnphr.seqids:
        presence_dict[seqid] = {
            'R1': False,
            'R2': False
        }
    # Iterate over all the matches
    for __, file_list in file_matches.items():
        for file_dict in file_list:
            for seqid in cowsnphr.seqids:
                if seqid in file_dict['blob_name']:
                    # Update the presence dictionary
                    if 'R1' in file_dict['blob_name']:
                        presence_dict[seqid]['R1'] = True
                    if 'R2' in file_dict['blob_name']:
                        presence_dict[seqid]['R2'] = True
                    # Add the dictionary to the lsit
                    if file_dict not in seqids:
                        seqids.append(file_dict)
    # Initialise a variable to track whether all the necessary query files
    # were located
    errors = False
    for seqid, presence in presence_dict.items():
        # Check if either the forward or reverse reads are missing
        if presence['R1'] is False or presence['R2'] is False:
            cowsnphr.seqid_errors.append(
            _('Could not locate one or more .fastq.gz files for SEQID {seqid}'
              .format(seqid=seqid))
            )
            cowsnphr.status = 'Error'
            cowsnphr.save()
            errors = True
    # Return None if any files were missing
    if errors:
        return None
    return seqids


def copy_blobs(
        blob_metadata_list: list,
        destination_container: str,
        destination_path: str):
    """
    Copy blobs from one or more containers into nested folders in a
    destination container 
    :param list blob_metadata_list: List metadata for blobs to be copied
    :param str destination container: Name of the container into which the
        blobs are to be copied
    """
    blob_service = BlockBlobService(
        account_key=settings.AZURE_ACCOUNT_KEY,
        account_name=settings.AZURE_ACCOUNT_NAME
    )
    blob_service.create_container(container_name=destination_container)
    for blob_metadata_dict in blob_metadata_list:
        # Set the new name of the blob as the destination folder and the file
        # name (without path information )
        blob_name = os.path.join(
            destination_path,
            os.path.basename(blob_metadata_dict["blob_name"])
        )
        blob_service.copy_blob(
            destination_container,
            blob_name,
            blob_metadata_dict["blob_download_link"]
        )


def create_batch_client():
    """
    Create a batch client using the stored credentials
    :return batch.BatchServiceClient batch_client: Azure batch client
    """
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )
    # Create a batch client for manipulating batch jobs, and pools
    batch_client = batch.BatchServiceClient(
        credentials,
        base_url=settings.BATCH_ACCOUNT_URL
    )
    return batch_client


def check_for_task_completion(
        task: COWSNPhRAzureTask,
        batch_client: batch.BatchServiceClient):
    """
    Check to see if the task is complete
    :param COWSNPhRAzureTask task: Current Azure task
    :param batch.BatchServiceClient batch_client: Azure batch client
    :return COWSNPhRRequest cowsnphr_request: Database entry of the current
    COWSNPhR analysis
    """
    # Retrieve the COWSNPhRRequest object corresponding to the
    # cowsnphr_request primary key
    cowsnphr_request = COWSNPhRRequest.objects.get(pk=task.cowsnphr.pk)
    # Set the container name appropriately
    batch_job_name = cowsnphr_request.container_name
    # Check if tasks related with this COWSNPhR job have finished.
    tasks_completed = True
    try:
        # Iterate over the tasks associated with the name of the batch job
        for cloudtask in batch_client.task.list(batch_job_name):
            print('cloudtasks', cloudtask.id, cloudtask.state)
            if cloudtask.state != batchmodels.TaskState.completed:

                tasks_completed = False
        print('tasks complete', tasks_completed)
        # Return if the tasks are complete
        if tasks_completed:
            return tasks_completed, cowsnphr_request

        # Locate all the batch pools
        pools = batch_client.pool.list()

        # Iterate over the pools
        for pool in pools:
            print('pool id', pool.id, 'batch job name', batch_job_name)
            # Ensure that the current batch job is being evaluated
            if pool.id != batch_job_name:
                continue

            # List all the nodes in the pool
            nodes = batch_client.compute_node.list(pool.id)
            for node in nodes:
                print('node', node.id, node.state)
                # Reboot the node if it becomes unusable
                if node.state == batchmodels.ComputeNodeState.unusable:
                    print(
                        "Rebooting node {node_id} due to "
                        "unusable state.".format(node_id=node.id)
                    )
                    batch_client.compute_node.reboot(
                        pool_id=pool.id,
                        node_id=node.id,
                        node_reboot_option=batchmodels.ComputeNodeRebootOption
                        .requeue
                    )
                    print("Node {node_id} is being rebooted.".format(
                        node_id=node.id
                        )
                    )
                    return False, cowsnphr_request

            # Proceed if there were batch resize errors. These usually
            # happen due to the quota getting reached
            if not pool.resize_errors:
                return tasks_completed, cowsnphr_request

            # Delete the pool, job, and task
            delete_pool_job(
                batch_client=batch_client,
                batch_job_name=batch_job_name
            )
            # Give the pool a chance to be deleted
            sleep(30)

            # Retry!
            # Set the local folder path
            local_folder = os.path.join(
                'olc_webportalv2',
                'media',
                batch_job_name
            )
            try:
                # Ensure that the necessary files are present
                _, __, mask = container_sanity(
                    container_name=cowsnphr_request.container_name
                )

                # Create the system call
                cmd = create_system_call(
                    mask=mask,
                    container_name=cowsnphr_request.container_name
                )

                # Resubmit the batch request
                generic_api_submit(
                    command=cmd,
                    container_name=cowsnphr_request.container_name,
                    vm_size='Standard_NV18ads_A10_v5',
                    analysis_type='COWSNPhR',
                    unique_id='FoodPort'
                )

                # Delete the task
                task.delete()

                # Recreate the task
                COWSNPhRAzureTask.objects.create(
                    cowsnphr=cowsnphr_request,
                    exit_code_file=os.path.join(
                        local_folder,
                        'exit_codes.txt'
                    )
                )
            except Exception as exc:
                file_handler = logging.FileHandler(
                    os.path.join(local_folder, 'error')
                )
                file_handler.setLevel(logging.INFO)
                logger.addHandler(file_handler)
                logger.exception(exc)
                capture_exception(exc)
                cowsnphr_request.status = 'Error'
                cowsnphr_request.error_list.append(exc)
                cowsnphr_request.save()
    # If something errors first time through, jobs can't get deleted. In that
    # case, give up.
    except Exception as exc:
        cowsnphr_request.status = 'Error'
        cowsnphr_request.error_list.append(exc)
        cowsnphr_request.save()
        # Delete task, so we don't keep iterating over it.
        COWSNPhRAzureTask.objects.filter(id=task.id).delete()

    return tasks_completed, cowsnphr_request


def delete_pool_job(
        batch_client: batch.BatchServiceClient,
        batch_job_name: str):
    """
    Delete the pool and job for an analyses
    :param batch.BatchServiceClient batch_client: Azure batch client
    :param str batch_job_name: Name of batch job and pool
    """
    # Initialise the exit code status to True
    exit_codes_good = True
    # Iterate through the tasks associated with the name of the batch job
    for cloud_task in batch_client.task.list(batch_job_name):
        # The only 'good' exit code is 0
        if cloud_task.execution_info.exit_code != 0:
            # A non-zero code sets the boolean to False
            exit_codes_good = False
    # Get rid of job and pool, so we don't waste big $$$ and do cleanup/get
    # files downloaded in tasks.
    batch_client.job.delete(job_id=batch_job_name)
    batch_client.pool.delete(pool_id=batch_job_name)
    return exit_codes_good


def task_succeeded(cowsnphr: COWSNPhRRequest):
    """
    Set the status to 'Complete', create a link to the necessary files, and
    send out an email (if requested)
    :param COWSNPhRRequest cowsnphr: Database entry of the current COWSNPhR
    analysis
    """
    cowsnphr.status = 'Complete'
    cowsnphr.save()
    # Send emails
    for email in cowsnphr.emails_array:
        send_email(
            subject='COWSNPhR Analysis "{name}" Complete'
                    .format(name=str(cowsnphr.project_name)),
            body=(
                'Dear {user},\n\n'
                'Your COWSNPhR analysis, "{name}", is complete.\n\n'
                'Reports are available for download: {archive_link}\n\n'
                'Best regards,\n'
                'The FoodPort development team'
                .format(
                    user=cowsnphr.user,
                    name=str(cowsnphr.project_name),
                    archive_link=cowsnphr.archive_download_link
                )),
            recipient=email
        )


def task_failed(cowsnphr):
    """
    Send an email (if anyone signed up to receive one), set the status to
    'Error'
    """
    # Send emails
    errors = '\n'.join(cowsnphr.error_list) if cowsnphr.error_list else 'None'
    for email in cowsnphr.emails_array:
        send_email(
            subject='COWSNPhR Analysis "{name}" Failed'
                    .format(name=str(cowsnphr.project_name)),
            body='Dear {user},\n'
                    'Your COWSNPhR analysis, "{name}", has failed.\n'
                    'The following errors were recorded: {errors}\n\n'
                    'Sorry for the inconvenience,\n'
                    'The FoodPort development team'
                    .format(
                        user=cowsnphr.user,
                        name=str(cowsnphr.project_name),
                        errors=errors,
                    ),
            recipient=email)
    # Update the model with the error status
    cowsnphr.status = 'Error'
    cowsnphr.save()


@shared_task
def check_cowsnphr_tasks():
    """
    Check the status of tasks. If task fails, perform clean-up. If task
    succeeds, perform necessary steps and clean-up
    """
    # Create a batch client
    batch_client = create_batch_client()

    # Retrieve all COWSNPhRAzureTask objects (they should be deleted after
    # they finish, so anything retrieved should be active)
    cowsnphr_tasks = COWSNPhRAzureTask.objects.filter()

    # Iterate over all the tasks to see if they are complete
    for task in cowsnphr_tasks:
        task_completed, cowsnphr = check_for_task_completion(
            task=task,
            batch_client=batch_client
        )

        # Allow the task to complete
        if not task_completed:
            continue

        # Clean up the job and pool if the task is complete
        exit_codes_good = delete_pool_job(
            batch_client=batch_client,
            batch_job_name=cowsnphr.container_name
        )

        # Perform appropriate actions depending on whether or not the task was
        # successful
        if exit_codes_good:
            post_processing(cowsnphr=cowsnphr)
            task_succeeded(cowsnphr=cowsnphr)
        else:
            task_failed(cowsnphr=cowsnphr)

        # Delete the COWSNPhRAzureTask
        COWSNPhRAzureTask.objects.filter(id=task.id).delete()


def post_processing(cowsnphr):
    """
    Run the required file manipulations (creation of zip files, SAS URLs, etc.)
    :param COWSNPhRRequest cowsnphr: Database entry of the current COWSNPhR
    analysis
    """
    # Download the report folders to the local system
    blob_client, local_folder, output_container = download_folders(
        cowsnphr=cowsnphr
    )
    # Load the reports into the COWSNPhR ReportOutputs model
    populate_models(
        cowsnphr=cowsnphr,
        local_folder=local_folder
    )

    # Create an archive of the reports
    archive_file = compress_outputs(
        cowsnphr=cowsnphr,
        local_folder=local_folder
    )

    # Upload the archive to blob storage
    sas_url = generate_archive_download_link(
        blob_client=blob_client,
        container_name=output_container,
        output_zipfile=archive_file
    )

    # Update the model with the link
    cowsnphr.archive_download_link = sas_url
    cowsnphr.save()

    # Remove the folder storing the outputs
    shutil.rmtree(local_folder)


def download_folders(cowsnphr):
    """
    Download the report folders from blob storage
    """
    # Create a client for listing blobs in the container
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Output container name is the container name
    output_container = cowsnphr.container_name
    # Set the name of the local folder
    local_folder = os.path.join(
        'olc_webportalv2',
        'media',
        cowsnphr.project_name
    )
    os.makedirs(local_folder, exist_ok=True)
    # List all the blobs in the container
    blobs = blob_client.list_blobs(container_name=output_container)
    # Store the names of the desired folders in a list
    desired_folders = [
        'alignments',
        'snv_matrix',
        'summary_tables',
        'tree_files',
        'vcf_files'
    ]

    # Iterate over all the desired folder names
    for folder in desired_folders:
        for blob in blobs:
            if fnmatch.fnmatch(
                blob.name,
                os.path.join(
                    'fastq',
                    folder,
                    '*'
                )
            ):
                os.makedirs(os.path.join(local_folder, folder), exist_ok=True)
                blob_client.get_blob_to_path(
                    container_name=output_container,
                    blob_name=blob.name,
                    file_path=os.path.join(
                        local_folder, folder, os.path.basename(blob.name)
                    )
                )
    return blob_client, local_folder, output_container


def populate_models(cowsnphr, local_folder):
    """
    Extract data from the reports, and store it in the COWSNPhRRequest model
    """
    # Alignment
    alignment_file = os.path.join(
        local_folder,
        'alignments',
        'alignment.fasta'
    )
    alignment = str()
    with open(alignment_file, encoding='utf-8') as alignment_handle:
        for line in alignment_handle:
            alignment += line.replace('\n', '<br>')

    # Amino acid summary table
    aa_summary_file = os.path.join(
        local_folder,
        'summary_tables',
        'aa_snv_sorted_table.xlsx'
    )
    aa_summary_table, aa_headers, aa_header_padding = read_spreadsheet(
        report=aa_summary_file,
        excel=True,
    )
    # Assembly Report
    assembly_file = os.path.join(
        local_folder,
        'summary_tables',
        'assembly_report.tsv'
    )
    assembly_report, assembly_headers, _ = read_spreadsheet(
        report=assembly_file
    )
    # Contig Summary
    contig_file = os.path.join(
        local_folder,
        'summary_tables',
        'contig_summary.tsv'
    )
    contig_summary, contig_headers, _ = read_spreadsheet(
        report=contig_file,
        index_col=False,
    )
    # Nucleotide summary table
    nt_summary_file = os.path.join(
        local_folder,
        'summary_tables',
        'nt_snv_sorted_table.xlsx'
    )
    nt_summary_table, nt_headers, nt_header_padding = read_spreadsheet(
        report=nt_summary_file,
        excel=True,
    )
    # Phylogenetic tree
    tree_file = os.path.join(
        local_folder,
        'tree_files',
        'best_tree.tre'
    )
    phylogenetic_tree = str()
    with open(tree_file, encoding='utf-8') as tree_handle:
        for line in tree_handle:
            phylogenetic_tree += line.replace('\n', '<br>')
    # SNV Matrix
    matrix_file = os.path.join(
        local_folder,
        'snv_matrix',
        'snv_matrix.tsv'
    )
    snv_matrix, snv_matrix_headers, _ = read_spreadsheet(report=matrix_file)
    # SNV Summary
    snv_summary_file = os.path.join(
        local_folder,
        'summary_tables',
        'snv_summary.tsv'
    )
    snv_summary, snv_summary_headers, _ = read_spreadsheet(
        report=snv_summary_file,
        index_col=False,
    )

    # Update the model with the extracted data
    cowsnphr.alignment = alignment
    cowsnphr.amino_acid_summary_table = aa_summary_table
    cowsnphr.amino_acid_headers = aa_headers
    cowsnphr.amino_acid_header_padding = aa_header_padding
    cowsnphr.assembly_report = assembly_report
    cowsnphr.assembly_headers = assembly_headers
    cowsnphr.contig_summary = contig_summary
    cowsnphr.contig_headers = contig_headers
    cowsnphr.nucleotide_summary_table = nt_summary_table
    cowsnphr.nucleotide_headers = nt_headers
    cowsnphr.nucleotide_header_padding = nt_header_padding
    cowsnphr.phylogenetic_tree = phylogenetic_tree
    cowsnphr.snv_matrix = snv_matrix
    cowsnphr.snv_matrix_headers = snv_matrix_headers
    cowsnphr.snv_summary = snv_summary
    cowsnphr.snv_summary_headers = snv_summary_headers
    cowsnphr.save()


def read_spreadsheet(
        report: str,
        excel: bool = False,
        index_col=True):
    """
    Use pandas to read in report data from a spreadsheet
    :param str report: Name and path of file to parse
    :param bool excel: Boolean of whether the report is an excel file
    :param bool transpose: Boolean of whether the table should be transposed
    :return str report: HTML string of the report
    """
    # Use pandas to read in the report
    if excel:
        report_dataframe = pd.read_excel(
            report,
            skip_blank_lines=True,
            index_col=False,
        ).fillna('')
    else:
        if not index_col:
            report_dataframe = pd.read_csv(
                report,
                sep='\t',
                index_col=False,
            ).fillna('')
        else:
            report_dataframe = pd.read_csv(
                report,
                sep='\t',
                skip_blank_lines=True
            ).fillna('')
    # Remove any "Unnamed: X" cells
    if not excel:
        for iterator in range(1000):
            try:
                report_dataframe.drop(
                    columns="Unnamed: {iterator}".format(iterator=iterator),
                    inplace=True
                )
            except KeyError:
                pass
    # Extract the headers from the dataframe
    if excel:
        report_headers = [
            column for column in report_dataframe.columns if 'Unnamed'
            not in column
        ]
        header_padding = []
        for integer in range(
            (len(list(report_dataframe.columns)) - len(report_headers))):
            header_padding.append(integer)
    else:
        header_padding = None
        report_headers = list(report_dataframe.columns)
    # Transpose the dataframe
    report_dataframe = report_dataframe.transpose()
    # Excel reports are special
    if not excel:
        # Convert the dataframe to a dictionary
        report_dict = report_dataframe.to_dict()
    else:
        # Initialise the dictionary to store the results
        report_dict = {}
        # Initialise a list to store the reference sequence
        ref_sequence = []
        # Iterate over the row number and the column data list for each row
        for pk, data_list in report_dataframe.items():
            # Initialise the row number in the dictionary as requied
            if pk not in report_dict:
                report_dict[pk] = []
            # Add each value in the list of columns to the list
            for value in data_list:
                report_dict[pk].append(value)
                if pk == 1:
                    ref_sequence.append(value)
    return report_dict, report_headers, header_padding


def compress_outputs(cowsnphr, local_folder):
    """
    Create a zip archive of the desired output folders
    """
    cowsnphr_archive_folder = os.path.join(
        'olc_webportalv2',
        'media',
        'cowsnphr_archives'
    )
    archive_file = os.path.join(
        cowsnphr_archive_folder, '{container_name}_{container_pk}'.format(
            container_name=cowsnphr.project_name,
            container_pk=cowsnphr.pk
        )
    )
    try:
        os.remove(os.path.join(local_folder, 'batch_config.txt'))
    except FileNotFoundError:
        pass
    shutil.make_archive(
        os.path.join(
            archive_file,
        ), 'zip',
        local_folder
    )
    return archive_file + '.zip'


def generate_archive_download_link(
        blob_client,
        container_name,
        output_zipfile,
        expiry=8):
    """
    Make a download link for a file that will be put into Azure blob storage,
    good for up to expiry days
    :param blob_client: Instance of azure.storage.blob.BlockBlobService
    :param container_name: Name of container you want to create.
    :param output_zipfile: Zipfile that you want to upload and for which you
        want to create a link.
    :param expiry: Number of days for which the link should be valid.
    :return: String of a link that allows people to download container.
    """
    blob_name = os.path.basename(output_zipfile)
    blob_client.create_blob_from_path(
        container_name=container_name,
        blob_name=blob_name,
        file_path=output_zipfile
    )
    sas_token = blob_client.generate_container_shared_access_signature(
        container_name=container_name,
        permission=BlobPermissions.READ,
        expiry=datetime.datetime.utcnow() + datetime.timedelta(days=expiry)
    )
    sas_url = blob_client.make_blob_url(
        container_name=container_name,
        blob_name=blob_name,
        sas_token=sas_token
    )
    return sas_url
