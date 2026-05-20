# Standard imports

"""
Tasks for the AmpliSeq app
"""

import datetime
from glob import glob
import logging
import os
import pathlib
import re
import shutil
import sys
import tarfile

# Django
from django.conf import settings
from django.db import DatabaseError
try:
    from django.utils.translation import gettext_lazy as _
except ImportError:
    from django.utils.translation import ugettext_lazy as _

# Celery Task Management
from celery import shared_task

# Azure
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

# Sentry
from sentry_sdk import capture_exception

# Local imports
from olc_webportalv2.ampliseq.models import (
    AmpliSeqAzureTask,
    AmpliSeqRequest,
    ContainerName
)
from olc_webportalv2.common.methods import generic_api_submit
from olc_webportalv2.primer_finder.methods import send_email

try:
    from azure_batch.azure_cli import AzureBatch
except ImportError:
    from olc_webportalv2.cowbat.methods import AzureBatch

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def find_containers():
    """
    Find all containers with names that match either a sequencing run, or
    have "ampliseq". Ignore any output containers
    """
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    containers = blob_client.list_containers()
    # Compile the regular expressions
    seq_run_re = re.compile('\d{6}')
    ampliseq_re = re.compile('ampliseq')
    # Use a list comprehension to find the matching container names
    run_set = {
        container.name for container in containers
        if ((seq_run_re.match(container.name) or ampliseq_re.match(container.name))
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


def upload_file(
        file,
        container_name,
        archive=None):
    """
    Upload the user-supplied files to blob storage
    """
    # Create a blob client
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Create the container (if required)
    blob_client.create_container(container_name)
    if not archive:
        # Upload the file to blob
        blob_client.create_blob_from_bytes(
            container_name=container_name,
            blob_name=file.name,
            blob=file.read()
        )
    # Upload an archive
    else:
        blob_client.create_blob_from_bytes(
            container_name=container_name,
            blob_name=archive,
            blob=file.read()
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
    # Create a blob client
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    blobs = blob_client.list_blobs(container_name=container_name)
    for blob in blobs:
        blob_client.get_blob_to_path(
            container_name=container_name,
            blob_name=blob.name,
            file_path=os.path.join(
                tmp_storage_dir, os.path.split(blob.name)[1]
            )
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
        cmd += '--metadata /datadrive/{container_name}/{metadata_file} ' \
            .format(
                container_name=ampliseq.container_name,
                metadata_file=ampliseq.metadata.name
            )
    if ampliseq.max_len:
        cmd += '--max_len {max_len} '.format(max_len=ampliseq.max_len)
    if ampliseq.min_len:
        cmd += '--min_len {min_len} '.format(min_len=ampliseq.min_len)
    if ampliseq.max_ee:
        cmd += '--max_ee {max_ee} '.format(max_ee=ampliseq.max_ee)
    if ampliseq.trunc_len_f:
        cmd += '--trunclenf {trunc_len_f} '.format(
            trunc_len_f=ampliseq.trunc_len_f
        )
    if ampliseq.trunc_len_r:
        cmd += '--trunclenr {trunc_len_r} '.format(
            trunc_len_r=ampliseq.trunc_len_r
        )
    if ampliseq.forward_primer:
        cmd += '--FW_primer {forward_primer} '.format(
            forward_primer=ampliseq.forward_primer
        )
    if ampliseq.reverse_primer:
        cmd += '--RV_primer {reverse_primer} '.format(
            reverse_primer=ampliseq.reverse_primer
        )
    if not ampliseq.forward_primer and not ampliseq.reverse_primer:
        cmd += '--skip_cutadapt '
    if ampliseq.taxonomy == 'dada':
        cmd += '--dada_ref_taxonomy {dada_ref_taxonomy} ' \
            .format(dada_ref_taxonomy=ampliseq.dada_ref_taxonomy)
    elif ampliseq.taxonomy == 'qiime':
        cmd += '--qiime_ref_taxonomy {qiime_ref_taxonomy} ' \
            .format(qiime_ref_taxonomy=ampliseq.qiime_ref_taxonomy)
    elif ampliseq.taxonomy == 'qiime_custom':
        cmd += '--classifier /datadrive/{container_name}/{classifier_file} ' \
            .format(
                container_name=ampliseq.container_file_name,
                classifier_file=ampliseq.classifier.name
            )
    if ampliseq.exclude_taxa:
        cmd += '--exclude_taxa {exclude_taxa} ' \
            .format(exclude_taxa=ampliseq.exclude_taxa)
    cmd += '; nextflow clean -k -f; rsync -a /datadrive/{container_name} ' \
        '$AZ_BATCH_TASK_WORKING_DIR/ && ' \
        'rm -rf /datadrive/{container_name}'.format(
            container_name=ampliseq.container_name
        )
    return cmd


def create_batch_config(
        ampliseq: AmpliSeqRequest,
        local_folder: str,
        cmd: str,
        vm_size='Standard_D32s_v3'):
    """
    Create the batch config file that is used by (the soon to be deprecated)
    AzureBatch
    :param AmpliSeqRequest ampliseq: AmpliSeqRequest model for the current
        analyses
    :param str local_folder: Name and path of folder in local storage storing
        the batch_config_file
    and into which error files are
    :param str cmd: Command line argument to use
    :param str vm_size: Size of virtual machine to create for batch analyses
    """
    # Create a configuration file to be used by the Azure batch script.
    batch_config_file = os.path.join(local_folder, 'batch_config.txt')
    with open(batch_config_file, 'w', encoding='utf-8') as batch_file:
        batch_file.write('BATCH_ACCOUNT_NAME:={}\n'.format(
            settings.BATCH_ACCOUNT_NAME
        ))
        batch_file.write('BATCH_ACCOUNT_KEY:={}\n'.format(
            settings.BATCH_ACCOUNT_KEY
        ))
        batch_file.write('BATCH_ACCOUNT_URL:={}\n'.format(
            settings.BATCH_ACCOUNT_URL
        ))
        batch_file.write('STORAGE_ACCOUNT_NAME:={}\n'.format(
            settings.AZURE_ACCOUNT_NAME
        ))
        batch_file.write('STORAGE_ACCOUNT_KEY:={}\n'.format(
            settings.AZURE_ACCOUNT_KEY
        ))
        batch_file.write('JOB_NAME:={}\n'.format(ampliseq.container_name))
        batch_file.write('VM_IMAGE:={}\n'.format(settings.AMPLISEQ_IMAGE))
        batch_file.write('VM_CLIENT_ID:={}\n'.format(settings.VM_CLIENT_ID))
        batch_file.write('VM_SECRET:={}\n'.format(settings.VM_SECRET))
        batch_file.write('VM_SIZE:={}\n'.format(vm_size))
        batch_file.write('VM_TENANT:={}\n'.format(settings.VM_TENANT))

        batch_file.write('CLOUDIN:={} {}\n'.format(
            os.path.join(
                ampliseq.container_name,
                '*.tar'
                ), ampliseq.container_name)
            )
        if ampliseq.metadata:
            batch_file.write('CLOUDIN:={} {}\n'.format(
                os.path.join(
                    ampliseq.container_name,
                    ampliseq.metadata.name),
                ampliseq.container_name
            ))
        if ampliseq.classifier:
            batch_file.write('CLOUDIN:={} {}\n'.format(
                os.path.join(
                    ampliseq.container_name,
                    ampliseq.classifier.name),
                ampliseq.container_name
            ))
        # Define which folder(s)/file(s) to copy to the output container
        batch_file.write('OUTPUT:={}/\n'.format(ampliseq.container_name))
        # Add the system call
        batch_file.write('COMMAND:={cmd}'.format(cmd=cmd))
        batch_file.write('\n')
    return batch_config_file


def submit_batch(
        ampliseq: AmpliSeqRequest,
        local_folder: str,
        batch_config_file: str,
        vm_size: str = 'Standard_D32s_v3'):
    """
    Use (the soon to be deprecated) AzureBatch class to submit the batch
    config file 
    :param AmpliSeqRequest ampliseq: AmpliSeqRequest model for the
    current analyses
    :param str local_folder: Name and path of folder in local storage storing
        the batch_config_file and into which error files are to be written
    :param str batch_config_file: Name and path of file containing necessary
        information for submitting batch jobs
    :param str vm_size: Size of virtual machine to use for batch analyses.
        Default is Standard_D32s_v3
    """
    try:
        # Submit the file to batch with our package.
        # azure_task = AzureBatch()
        # azure_task.main(
        #     configuration_file=batch_config_file,
        #     job_name=ampliseq.container_name,
        #     output_dir=os.path.join('olc_webportalv2', 'media'),
        #     settings=settings,
        #     keep_input_container=True,
        #     download_output_files=False,
        #     no_clean=True,
        #     vm_size=vm_size,
        #     vm_image='AMPLISEQ_IMAGE'
        # )

        # Create an AmpliSeqAzureTask entry, so the progress of the analyses
        # can be followed by
        AmpliSeqAzureTask.objects.create(
            ampliseq=ampliseq,
            exit_code_file=os.path.join(local_folder, 'exit_codes.txt'))
    except Exception as exc:
        file_handler = logging.FileHandler(os.path.join(local_folder, 'error'))
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        logger.exception(exc)
        capture_exception(exc)
        ampliseq.status = 'Error'
        ampliseq.error_list.append(exc)
        ampliseq.save()


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

    # Check to see which version of AzureBatch is to be used
    # if 'azure_batch' in sys.modules:
    #     AzureBatch(
    #         command_file=cmd,
    #         vm_size='Standard_D32s_v3',
    #         settings=settings,
    #         container=ampliseq.container_name,
    #         path=None,
    #         upload_folder=None,
    #         input_file_pattern=None,
    #         bulk_input_file_pattern=None,
    #         download_file_pattern=None,
    #         unique_id=ampliseq.pk,
    #         worker=True,
    #         no_tidy=True
    #     )
    # # Create an AmpliSeqAzureTask entry, so the progress of the analyses
    # # can be followed by FoodPort
    # AmpliSeqAzureTask.objects.create(
    #     ampliseq=ampliseq,
    #     exit_code_file=os.path.join(local_folder, 'exit_codes.txt'))
    # else:
    #     # Create the batch_config.txt file
    #     batch_config_file = create_batch_config(
    #         ampliseq=ampliseq,
    #         local_folder=local_folder,
    #         cmd=cmd,
    #     )
    #     package_files_for_upload(
    #         container_name=ampliseq.container_name
    #     )
    #     # Create a batch request
    #     submit_batch(
    #         ampliseq=ampliseq,
    #         local_folder=local_folder,
    #         batch_config_file=batch_config_file,
    #         vm_size='Standard_D32s_v3'
    #     )


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
        task: AmpliSeqAzureTask,
        batch_client: batch.BatchServiceClient):
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
            if cloudtask.state != batchmodels.TaskState.completed:
                tasks_completed = False
    # If something errors first time through, jobs can't get deleted. In that
    #   case, give up.
    except Exception as exc:
        AmpliSeqRequest.objects.filter(
            pk=task.ampliseq.pk).update(status='Error', errors=exc)
        # Delete task, so we don't keep iterating over it.
        AmpliSeqAzureTask.objects.filter(id=task.id).delete()

    return tasks_completed, ampliseq_request


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
    Create SAS URLs for the report, tracce, and timeline files
    """
    # Set the name of the output container and run folder
    output_container = ampliseq.container_name + '-output'
    # Create the blob service client for manipulating blobs
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Generate an SAS url with read access that users will be able to use to
    # download their reports.
    sas_token = blob_client.generate_container_shared_access_signature(
        container_name=output_container,
        permission=BlobPermissions.READ,
        expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8)
    )

    # Create a variable to store part of the path information
    # (decreases line length)
    folder_path = '{container}/reports/{container}'.format(
        container=ampliseq.container_name
    )

    # Create SAS URLs for both the execution report,the execution trace, and
    # execution timeline
    execution_report_sas_url = blob_client.make_blob_url(
        container_name=output_container,
        blob_name='{folder_path}_execution_report.html'.format(
            folder_path=folder_path
        ),
        sas_token=sas_token
    )
    execution_trace_sas_url = blob_client.make_blob_url(
        container_name=output_container,
        blob_name='{folder_path}_execution_trace.txt'.format(
            folder_path=folder_path
        ),
        sas_token=sas_token
    )
    execution_timeline_sas_url = blob_client.make_blob_url(
        container_name=output_container,
        blob_name='{folder_path}_execution_timeline.html'.format(
            folder_path=folder_path
        ),
        sas_token=sas_token
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
    # Set the folder name where the HTML reports are to be written
    local_folder = os.path.join('olc_webportalv2', 'templates', 'ampliseq')
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    blob_client.get_blob_to_path(
        '{container}-output'.format(container=ampliseq.container_name),
        '{container}/reports/{container}_execution_report.html'.format(
            container=ampliseq.container_name
        ),
        os.path.join(
            local_folder,
            '{container_name}_execution_report.html'.format(
                container_name=ampliseq.container_name
            )
        )
    )
    blob_client.get_blob_to_path(
        '{container}-output'.format(container=ampliseq.container_name),
        '{container}/reports/{container}_execution_timeline.html'.format(
            container=ampliseq.container_name
        ),
        os.path.join(
            local_folder,
            '{container}_execution_timeline.html'.format(
                container=ampliseq.container_name
            )
        )
    )


def download_results(ampliseq: AmpliSeqRequest):
    """
    Download the results to disk and create SAS URL
    """
    # Set the folder name where the HTML reports are to be written
    local_folder = os.path.join('olc_webportalv2', 'media', 'ampliseq')
    # print(local_folder)
    results_folder = os.path.join(local_folder, ampliseq.container_name)
    # results_dir: the directory where results will be saved by the system call
    results_dir = 'results2'
    # print(results_folder)
    os.makedirs(results_folder, exist_ok=True)
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )

    # List all the results in the container
    blobs = blob_client.list_blobs(container_name=ampliseq.container_name)

    for blob in blobs:
        # Only look at files within {container_name}/{results_dir}:
        if '/'.join(pathlib.Path(blob.name).parts[0:2]) == \
                "{container}/{results_dir}".format(
                    container=ampliseq.container_name, results_dir=results_dir
                ):
            file_name = pathlib.Path(blob.name).name
            # Obtain the path between '{container_name}/{results_dir}' and the
            # file name
            subpath = '/'.join(pathlib.Path(blob.name).parts[2:-1])
            path_to_create = os.path.join(results_folder, subpath)
            os.makedirs(path_to_create, exist_ok=True)
            blob_client.get_blob_to_path(
                container_name=ampliseq.container_name,
                blob_name=blob.name,
                file_path=os.path.join(path_to_create + '/' + file_name)
            )
  
    # With that done, create a zipfile.
    blob_name = os.path.join(local_folder, ampliseq.container_name + '.zip')
    # print(blob_name)
    shutil.make_archive(os.path.splitext(blob_name)[0], 'zip', results_folder)
    sas_url = generate_download_link(blob_client=blob_client,
                                     container_name=ampliseq.container_name,
                                     output_zipfile=blob_name,
                                     expiry=730)
    # print(sas_url)
    ampliseq.results_download_link = sas_url
    # print(ampliseq.results_download_link)
    ampliseq.save()
    shutil.rmtree(results_folder)
    os.remove(blob_name)


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
            subject='AmpliSeq Analysis "{name}" Complete'
                    .format(name=str(ampliseq.project_name)),
            body='Dear {user},\n'
                 'Your AmpliSeq analysis, "{name}", is complete.\n'
                 'The AmpliSeq analysis results are available '
                 'here: {results_url}.\n'
                 'The AmpliSeq report is available here: {report_url}.\n'
                 'The AmpliSeq trace is available here: {trace_url}.\n'
                 'The AmpliSeq timeline is available here: {timeline_url}\n\n'
                 'Best regards,\n'
                 'The FoodPort development team'
                 .format(
                     user=ampliseq.user,
                     name=str(ampliseq.project_name),
                     results_url=ampliseq.results_download_link,
                     report_url=ampliseq.execution_report_download_link,
                     trace_url=ampliseq.execution_trace_download_link,
                     timeline_url=ampliseq.execution_timeline_download_link
                 ),
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
            subject='AmpliSeq Analysis "{name}" Failed'
                    .format(name=str(ampliseq.project_name)),
            body='Dear {user},\n'
                    'Your AmpliSeq analysis, "{name}", has failed.\n'
                    'The following errors were recorded: {errors}\n\n'
                    'Sorry for the inconvenience,\n'
                    'The FoodPort development team'
                    .format(
                        user=ampliseq.user,
                        name=str(ampliseq.project_name),
                        errors=errors,
                    ),
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


def generate_download_link(
            blob_client,
            container_name,
            output_zipfile,
            expiry=8):
    """
    Make a download link for a file that will be put into Azure blob storage,
    good for up to expiry days
    :param blob_client: Instance of azure.storage.blob.BlockBlobService
    :param container_name: Name of container you want to create.
    :param output_zipfile: Zipfile you want to upload and create a link for.
    :param expiry: Number of days link should be valid for.
    :return: String of a link that allows people to download container.
    """
    blob_client.create_container(container_name)
    blob_name = os.path.split(output_zipfile)[1]
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
