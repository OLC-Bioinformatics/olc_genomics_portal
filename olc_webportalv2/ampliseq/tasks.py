"""
Tasks for the AmpliSeq app
"""

# Standard imports
from glob import glob
import logging
import os
import pathlib
import re
import shutil
import sys
import tarfile

# Django
from django.db import DatabaseError
from django.utils.translation import gettext_lazy as _

# Celery Task Management
from celery import shared_task

# Azure
from azure.batch import BatchClient
from azure.batch.models import BatchTaskState
from azure.core.exceptions import (
    AzureError,
    HttpResponseError,
    ResourceNotFoundError,
)

# Sentry
from sentry_sdk import capture_exception

# Local imports
from olc_webportalv2.ampliseq.models import (
    AmpliSeqAzureTask,
    AmpliSeqRequest,
    ContainerName
)
from olc_webportalv2.common.methods import (
    create_batch_client,
    create_blob_service,
    create_blob_from_bytes,
    download_blob_to_path,
    generate_download_link,
    create_container,
    generic_api_submit,
    send_email,
    upload_blob_from_path,
)

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def find_containers():
    """
    Find all containers with names that match either a sequencing run, or
    have "ampliseq".
    """
    blob_service = create_blob_service()
    containers = blob_service.list_containers()
    # Compile the regular expressions
    seq_run_re = re.compile(r'\d{6}')
    ampliseq_re = re.compile('ampliseq')
    # Use a list comprehension to find the matching container names
    run_set = {
        container.name for container in containers
        if (
            (seq_run_re.match(container.name) or
             ampliseq_re.match(container.name))
        )
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
        ResourceNotFoundError,
        HttpResponseError,
        AzureError,
        DatabaseError
    ) as exc:
        capture_exception(exc)


def upload_file(
        file,
        container_name,
        archive=None):
    """
    Upload the user-supplied files to blob storage
    """
    blob_service = create_blob_service()
    create_container(container_name, blob_service)
    blob_name = archive if archive else file.name
    create_blob_from_bytes(
        container_name=container_name,
        blob_name=blob_name,
        data=file.read(),
        blob_service_client=blob_service,
    )


def download_sequence_files(container_name: str):
    """
    Download the files in the supplied container name in blob storage
    :param str container_name: Name of the supplied container in blob storage
    """
    download_dir = os.path.join(
        os.sep,
        'data',
        'web',
        'olc_webportalv2',
        'media',
        'files_for_upload'
    )
    tmp_storage_dir = os.path.join(download_dir, container_name)
    # Delete the temporary folder if it already exists (mostly for
    # development purposes)
    try:
        # Delete the temporary storage folder
        shutil.rmtree(tmp_storage_dir)
    except FileNotFoundError:
        pass
    os.makedirs(tmp_storage_dir, exist_ok=True)
    blob_service = create_blob_service()
    container_client = blob_service.get_container_client(container_name)
    blobs = container_client.list_blobs()
    for blob in blobs:
        download_blob_to_path(
            container_name=container_name,
            blob_name=blob.name,
            file_path=os.path.join(tmp_storage_dir, os.path.split(blob.name)[1]),
            blob_service_client=blob_service,
        )
    return tmp_storage_dir


def package_files_for_upload(container_name: str):
    """
    Package files into a .tar archive and upload to blob storage
    :param str container_name: Name of the supplied container in blob storage
    """
    # Download the sequence files from blob
    tmp_storage_dir = download_sequence_files(container_name=container_name)
    # Locate all the downloaded files
    files = glob(os.path.join(tmp_storage_dir, '*'))
    # Set the name of the archive file
    tar_file = os.path.join(tmp_storage_dir, container_name + '.tar')
    # Create the archive
    with tarfile.open(tar_file, 'w') as tar_obj:
        for file in files:
            tar_obj.add(
                file,
                arcname=os.path.basename(file)
           )
    # Upload the tar file to blob storage
    with open(tar_file, 'rb') as tar:
        upload_file(
            file=tar,
            container_name=container_name,
            archive=os.path.basename(tar_file)
        )
    # Delete the temporary storage folder
    shutil.rmtree(tmp_storage_dir)


def create_system_call(ampliseq: AmpliSeqRequest):
    """
    Extract the user-provided arguments from the database, and create the
    system call to be performed on the batch VM
    source $CONDA/activate && nextflow run
    /opt/nf-core-ampliseq-2.6.1/workflow/
    -profile singularity
    -resume
    --input $AZ_BATCH_NODE_MOUNTS_DIR/ampliseq-191224/data/raw/fastq_gz/
    --FW_primer CCTACGGGNGGCWGCAG
    --RV_primer GACTACHVGGGTATCTAATCC
    --outdir $AZ_BATCH_NODE_MOUNTS_DIR/ampliseq-191224/results/
    --max_ee 100000
    --dada_ref_taxonomy silva=132
    --exclude_taxa mitochondria,chloroplast
    --trunclenf 277
    --trunclenr 234

    :param AmpliSeqRequest ampliseq: AmpliSeqRequest model for the current
        analysis
    """
    # Copy the files to /datadrive, perform the analyses there, and move
    # everything back once the pipeline is complete
    # Using 'results2' as the output dir because conflict with 'results'
    cmd = (
        'source $CONDA/activate && '
        'cp -R $AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/ /datadrive/ && '
        'cd /datadrive/{container_name}/ && '.format(
            container_name=ampliseq.container_name
        )
    )
    # If the sequence files have been archived, unarchive them
    if 'azure_batch' not in sys.modules:
        cmd += (
            'tar xf /datadrive/{container_name}/{container_name}.tar && '
            'rm -rf /datadrive/{container_name}/results/ ; '
            .format(
                container_name=ampliseq.container_name
            )
        )
    cmd += (
        'nextflow run /datadrive/nf-core-ampliseq-2.7.1/workflow/ '
        '-profile singularity '
        '-resume '
        '-w /datadrive/{container}/work '
        '-with-report '
        '/datadrive/{container}/reports/{container}_execution_report.html '
        '-with-trace '
        '/datadrive/{container}/reports/{container}_execution_trace.txt '
        '-with-timeline '
        '/datadrive/{container}/reports/{container}_execution_timeline.html '
        '--input_folder /datadrive/{container} '
        '--outdir /datadrive/{container}/{results_dir} '.format(
                container=ampliseq.container_name,
                results_dir='results2'
            )
    )
    if ampliseq.metadata:
        cmd += (
            f'--metadata /datadrive/{ampliseq.container_name}/'
            f'{ampliseq.metadata.name} '
        )
    if ampliseq.max_len:
        cmd += f'--max_len {ampliseq.max_len} '
    if ampliseq.min_len:
        cmd += f'--min_len {ampliseq.min_len} '
    if ampliseq.max_ee:
        cmd += f'--max_ee {ampliseq.max_ee} '
    if ampliseq.trunc_len_f:
        cmd += f'--trunclenf {ampliseq.trunc_len_f} '
    if ampliseq.trunc_len_r:
        cmd += f'--trunclenr {ampliseq.trunc_len_r} '
    if ampliseq.forward_primer:
        cmd += f'--FW_primer {ampliseq.forward_primer} '
    if ampliseq.reverse_primer:
        cmd += f'--RV_primer {ampliseq.reverse_primer} '
    if not ampliseq.forward_primer and not ampliseq.reverse_primer:
        cmd += '--skip_cutadapt '
    if ampliseq.taxonomy == 'dada':
        cmd += f'--dada_ref_taxonomy {ampliseq.dada_ref_taxonomy} '
    elif ampliseq.taxonomy == 'qiime':
        cmd += f'--qiime_ref_taxonomy {ampliseq.qiime_ref_taxonomy} '
    elif ampliseq.taxonomy == 'qiime_custom':
        cmd += (
            f'--classifier /datadrive/{ampliseq.container_name}/'
            f'{ampliseq.classifier.name} '
        )
    if ampliseq.exclude_taxa:
        cmd += f'--exclude_taxa {ampliseq.exclude_taxa} '

    # Add the cleanup commands to the end of the system call
    cmd += (
        f'; nextflow_status=$?; '
        f'nextflow clean -k -f || true; '
        f'mkdir -p '
        f'$AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/reports '
        f'$AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/results2; '
        f'rsync -a /datadrive/{ampliseq.container_name}/reports/ '
        f'$AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/reports/; '
        f'rsync -a /datadrive/{ampliseq.container_name}/results2/ '
        f'$AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/results2/; '
        f'rm -rf /datadrive/{ampliseq.container_name}; '
        f'exit $nextflow_status'
    )

    return cmd


@shared_task
def run_ampliseq_batch(primary_key: int):
    """
    Run the necessary functions to submit the AmpliSeq request to batch
    :param int primary_key: Primary key of the AmpliSeqRequest being processed
    """
    ampliseq = AmpliSeqRequest.objects.get(pk=primary_key)
    # Create the system call
    cmd = create_system_call(ampliseq=ampliseq)
    # Create a folder into which any error code files are to be written
    local_folder = os.path.join('olc_webportalv2', 'media', str(ampliseq))
    os.makedirs(local_folder, exist_ok=True)

    # Submit the command to the AzureBatch service
    generic_api_submit(
        command=cmd,
        container_name=ampliseq.container_name,
        vm_size='Standard_D32s_v3',
        analysis_type='AmpliSeq',
        unique_id='FoodPort'
        )

    # Create an AmpliSeqAzureTask entry, so the progress of the analyses
    # can be followed by FoodPort
    AmpliSeqAzureTask.objects.create(
        ampliseq=ampliseq,
        exit_code_file=os.path.join(local_folder, 'exit_codes.txt'))


def check_for_task_completion(
    task: AmpliSeqAzureTask,
    batch_client: BatchClient
) -> tuple[bool, AmpliSeqRequest]:
    """
    Check to see if the task is complete
    :param AmpliSeqAzureTask task: Current Azure task
    :param batch.BatchServiceClient batch_client: Azure batch client
    :return AmpliSeqRequest ampliseq_request: Database entry of the current
        AmpliSeq analysis
    """
    # Retrieve the AmpliSeqRequests object corresponding to the
    #   ampliseq_request primary key
    ampliseq_request = AmpliSeqRequest.objects.get(pk=task.ampliseq.pk)
    # Set the container name appropriately
    batch_job_name = ampliseq_request.container_name
    # Check if tasks related with this AmpliSeq job have finished.
    tasks_completed = True
    try:
        for cloudtask in batch_client.task.list(batch_job_name):
            if cloudtask.state != BatchTaskState.COMPLETED:
                tasks_completed = False
    # If something errors first time through, jobs can't get deleted. In that
    #   case, give up.
    except Exception as exc:
        AmpliSeqRequest.objects.filter(
            pk=task.ampliseq.pk).update(status='Error', errors=[str(exc)])
        # Delete task, so we don't keep iterating over it.
        AmpliSeqAzureTask.objects.filter(id=task.id).delete()

    return tasks_completed, ampliseq_request


def delete_pool_job(
    batch_client: BatchClient,
    batch_job_name: str
) -> bool:
    """
    Delete the pool and job for an analyses
    :param batch.BatchServiceClient batch_client: Azure batch client
    :param str batch_job_name: Name of batch job and pool
    :return bool exit_codes_good: True if all tasks have exit code 0, False
    """
    # Initialise the exit code status to True
    exit_codes_good = True
    # Iterate through the tasks associated with the name of the batch job
    for cloudtask in batch_client.task.list(batch_job_name):
        # The only 'good' exit code is 0
        if cloudtask.execution_info.exit_code != 0:
            # A non-zero code sets the boolean to False
            exit_codes_good = False
    # Get rid of job and pool, so we don't waste big $$$ and do cleanup/get
    # files downloaded in tasks.
    batch_client.job.delete(job_id=batch_job_name)
    batch_client.pool.delete(pool_id=batch_job_name)
    return exit_codes_good


def create_sas_urls(ampliseq):
    """
    Create SAS URLs for the report, trace, and timeline files.
    """
    # Create a blob service client
    blob_service = create_blob_service()

    folder_path = '{container}/reports/{container}'.format(
        container=ampliseq.container_name
    )

    execution_report_sas_url = generate_download_link(
        blob_service_client=blob_service,
        container_name=ampliseq.container_name,
        blob_name=f'{folder_path}_execution_report.html',
        expiry=8,
    )
    execution_trace_sas_url = generate_download_link(
        blob_service_client=blob_service,
        container_name=ampliseq.container_name,
        blob_name=f'{folder_path}_execution_trace.txt',
        expiry=8,
    )
    execution_timeline_sas_url = generate_download_link(
        blob_service_client=blob_service,
        container_name=ampliseq.container_name,
        blob_name=f'{folder_path}_execution_timeline.html',
        expiry=8,
    )
    return (
        execution_report_sas_url,
        execution_trace_sas_url,
        execution_timeline_sas_url
    )


def download_reports(ampliseq: AmpliSeqRequest):
    """
    Download the HTML reports to disk
    """
    local_folder = os.path.join('olc_webportalv2', 'templates', 'ampliseq')
    blob_service = create_blob_service()

    download_blob_to_path(
        container_name=ampliseq.container_name,
        blob_name=f'{ampliseq.container_name}/reports/'
                  f'{ampliseq.container_name}_execution_report.html',
        file_path=os.path.join(
            local_folder,
            f'{ampliseq.container_name}_execution_report.html'
        ),
        blob_service_client=blob_service,
    )
    download_blob_to_path(
        container_name=ampliseq.container_name,
        blob_name=f'{ampliseq.container_name}/reports/'
                  f'{ampliseq.container_name}_execution_timeline.html',
        file_path=os.path.join(
            local_folder,
            f'{ampliseq.container_name}_execution_timeline.html'
        ),
        blob_service_client=blob_service,
    )


def download_results(ampliseq: AmpliSeqRequest):
    """
    Download the results to disk and create SAS URL
    """
    local_folder = os.path.join('olc_webportalv2', 'media', 'ampliseq')
    results_folder = os.path.join(local_folder, ampliseq.container_name)
    results_dir = 'results2'

    os.makedirs(results_folder, exist_ok=True)
    blob_service = create_blob_service()

    # List all blobs in the container
    blobs = blob_service.get_container_client(
        ampliseq.container_name
    ).list_blobs()

    # Boolean to track if any blobs were found for the results directory
    results_found = False

    # Iterate through the blobs and download those that are in the results
    # directory
    for blob in blobs:
        # Check if the blob is in the results directory
        if '/'.join(pathlib.Path(blob.name).parts[0:2]) == \
                f"{ampliseq.container_name}/{results_dir}":
            # Set the boolean to True since we found at least one result
            results_found = True

            # Create the appropriate subpath and ensure the directory exists
            file_name = pathlib.Path(blob.name).name
            subpath = '/'.join(pathlib.Path(blob.name).parts[2:-1])
            path_to_create = os.path.join(results_folder, subpath)
            os.makedirs(path_to_create, exist_ok=True)

            # Download the blob to the appropriate path
            download_blob_to_path(
                container_name=ampliseq.container_name,
                blob_name=blob.name,
                file_path=os.path.join(path_to_create, file_name),
                blob_service_client=blob_service,
            )

    # If no results were found, raise a FileNotFoundError
    if not results_found:
        raise FileNotFoundError(
            f"No results found in container {ampliseq.container_name} "
            f"under {ampliseq.container_name}/{results_dir}"
        )

    # Create a zip file of the results
    zip_path = os.path.join(local_folder, ampliseq.container_name + '.zip')
    shutil.make_archive(os.path.splitext(zip_path)[0], 'zip', results_folder)

    # Upload the zip file to blob storage
    upload_blob_from_path(
        container_name=ampliseq.container_name,
        blob_name=os.path.basename(zip_path),
        file_path=zip_path,
        blob_service_client=blob_service,
    )

    sas_url = generate_download_link(
        blob_service_client=blob_service,
        container_name=ampliseq.container_name,
        blob_name=os.path.basename(zip_path),
        expiry=730,
    )

    # Update the AmpliSeqRequest with the SAS URL for the results
    ampliseq.results_download_link = sas_url
    ampliseq.save()
    shutil.rmtree(results_folder)
    os.remove(zip_path)


def task_succeeded(ampliseq: AmpliSeqRequest):
    """
    Set the status to 'Complete', create a link to the necessary files, and
    send out an email
    (if requested)
    :param AmpliSeqRequest ampliseq_request: Database entry of the current
        AmpliSeq analysis
    """
    ampliseq.status = 'Complete'
    # Create links to the report, trace, and timeline
    ampliseq.execution_report_download_link, \
        ampliseq.execution_trace_download_link, \
        ampliseq.execution_timeline_download_link = create_sas_urls(
            ampliseq=ampliseq
    )
    ampliseq.save()
    # Download the reports
    download_reports(ampliseq=ampliseq)
    download_results(ampliseq=ampliseq)
    # Send emails
    for email in ampliseq.emails_array:
        send_email(
            subject=f'AmpliSeq Analysis "{ampliseq.project_name}" Complete',
            body=f'Dear {ampliseq.user},\n'
                 f'Your AmpliSeq analysis, "{ampliseq.project_name}", '
                 'is complete.\n'
                 f'The AmpliSeq analysis results are available '
                 f'here: {ampliseq.results_download_link}.\n'
                 f'The AmpliSeq report is available here: '
                 f'{ampliseq.execution_report_download_link}.\n'
                 f'The AmpliSeq trace is available here: '
                 f'{ampliseq.execution_trace_download_link}.\n'
                 f'The AmpliSeq timeline is available here: '
                 f'{ampliseq.execution_timeline_download_link}\n\n'
                 'Best regards,\n'
                 'The FoodPort development team',
            recipient=email
        )


def task_failed(ampliseq):
    """
    Send an email (if anyone signed up to receive one), set the status
    to 'Error'
    """
    # Send emails
    errors = '\n'.join(ampliseq.error_list) if ampliseq.error_list else 'None'
    for email in ampliseq.emails_array:
        send_email(
            subject=f'AmpliSeq Analysis "{ampliseq.project_name}" Failed',
            body=f'Dear {ampliseq.user},\n'
                 f'Your AmpliSeq analysis, "{ampliseq.project_name}", '
                 'has failed.\n'
                 f'The following errors were recorded: {errors}\n\n'
                 'Sorry for the inconvenience,\n'
                 'The FoodPort development team',
            recipient=email)

    # Update the model with the error status
    ampliseq.status = 'Error'
    ampliseq.save()

@shared_task
def check_ampliseq_tasks():
    """
    Check the status of tasks. If task fails, perform clean-up. If task
    succeeds, perform
    necessary steps and clean-up
    """
    # Create a batch client
    batch_client = create_batch_client()
    # Retrieve all AmpliSeqAzureTask objects (they should be deleted after
    # they finish, so anything retrieved should be active)
    ampliseq_tasks = AmpliSeqAzureTask.objects.filter()
    # Iterate over all the tasks to see if they are complete
    for task in ampliseq_tasks:
        task_completed, ampliseq = check_for_task_completion(
            task=task,
            batch_client=batch_client
        )
        # Allow the task to complete
        if not task_completed:
            continue

        # Clean up the job and pool if the task is complete
        exit_codes_good = delete_pool_job(
            batch_client=batch_client,
            batch_job_name=ampliseq.container_name
        )

        # Perform appropriate actions depending on whether or not the task was
        # successful
        if exit_codes_good:
            task_succeeded(ampliseq=ampliseq)
        else:
            task_failed(ampliseq=ampliseq)

        # Delete the AmpliSeqAzureTask
        AmpliSeqAzureTask.objects.filter(id=task.id).delete()
