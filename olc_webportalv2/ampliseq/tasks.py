"""Celery tasks and helpers for AmpliSeq analyses."""

# Standard imports
import logging
import os
import pathlib
import re
import shutil

# Celery Task Management
from celery import shared_task

# Django
from django.db import DatabaseError

# Sentry
from sentry_sdk import capture_exception

# Local imports
from olc_webportalv2.ampliseq.models import (
    AmpliSeqAzureTask,
    AmpliSeqRequest,
    ContainerName,
)
from olc_webportalv2.common.batch_client import BatchServiceClient
from olc_webportalv2.common.methods import (
    create_batch_client,
    generic_api_submit,
    send_email,
)
from olc_webportalv2.common.storage_client import (
    StorageServiceError,
    get_storage_client,
)

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def find_containers():
    """Return storage containers matching sequencing runs or AmpliSeq."""
    blob_service = get_storage_client()
    containers = blob_service.list_containers()
    # Compile the regular expressions
    seq_run_re = re.compile(r"\d{6}")
    ampliseq_re = re.compile("ampliseq")
    # Use a list comprehension to find the matching container names
    run_set = {
        container.name
        for container in containers
        if (
            seq_run_re.match(container.name)
            or ampliseq_re.match(container.name)
        )
    }
    return sorted(run_set)


@shared_task
def refresh_container_names():
    """Synchronize AmpliSeq container names with storage."""
    try:
        # Find all the containers
        run_list = set(find_containers())
        # Get the current container names in the database
        current_names = set(
            ContainerName.objects.values_list("container_name", flat=True)
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
    except (StorageServiceError, DatabaseError) as exc:
        capture_exception(exc)


def upload_file(file, container_name, archive=None):
    """Upload a file to a storage container.

    Args:
        file: File-like object to upload.
        container_name: Destination storage container.
        archive: Optional object name to use instead of the file name.

    """
    blob_service = get_storage_client()
    blob_service.ensure_container(container_name)
    blob_name = archive if archive else file.name
    blob_service.upload_bytes(
        container_name,
        blob_name,
        file.read(),
        content_type=getattr(file, "content_type", None),
    )


def create_system_call(ampliseq: AmpliSeqRequest):
    """Build the Nextflow command for an AmpliSeq request.

    Args:
        ampliseq: AmpliSeq request being submitted.

    Returns:
        Shell command for execution on the Batch node.

    """
    # Copy the files to /datadrive, perform the analyses there, and move
    # everything back once the pipeline is complete
    # Using 'results2' as the output dir because conflict with 'results'
    cmd = (
        "source $CONDA/activate && "
        f"cp -R $AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/ "
        f"/datadrive/ && cd /datadrive/{ampliseq.container_name}/ && "
    )
    # If the sequence files have been archived, unarchive them
    cmd += (
        f"if [ -f /datadrive/{ampliseq.container_name}/"
        f"{ampliseq.container_name}.tar ]; then "
        f"tar xf /datadrive/{ampliseq.container_name}/"
        f"{ampliseq.container_name}.tar && "
        f"rm -rf /datadrive/{ampliseq.container_name}/results/; fi; "
    )
    cmd += (
        "nextflow run /datadrive/nf-core-ampliseq-2.7.1/workflow/ "
        "-profile singularity "
        "-resume "
        f"-w /datadrive/{ampliseq.container_name}/work "
        "-with-report "
        f"/datadrive/{ampliseq.container_name}/reports/"
        f"{ampliseq.container_name}_execution_report.html "
        "-with-trace "
        f"/datadrive/{ampliseq.container_name}/reports/"
        f"{ampliseq.container_name}_execution_trace.txt "
        "-with-timeline "
        f"/datadrive/{ampliseq.container_name}/reports/"
        f"{ampliseq.container_name}_execution_timeline.html "
        f"--input_folder /datadrive/{ampliseq.container_name} "
        f"--outdir /datadrive/{ampliseq.container_name}/results2 "
    )
    if ampliseq.metadata:
        cmd += (
            f"--metadata /datadrive/{ampliseq.container_name}/"
            f"{ampliseq.metadata.name} "
        )
    if ampliseq.max_len:
        cmd += f"--max_len {ampliseq.max_len} "
    if ampliseq.min_len:
        cmd += f"--min_len {ampliseq.min_len} "
    if ampliseq.max_ee:
        cmd += f"--max_ee {ampliseq.max_ee} "
    if ampliseq.trunc_len_f:
        cmd += f"--trunclenf {ampliseq.trunc_len_f} "
    if ampliseq.trunc_len_r:
        cmd += f"--trunclenr {ampliseq.trunc_len_r} "
    if ampliseq.forward_primer:
        cmd += f"--FW_primer {ampliseq.forward_primer} "
    if ampliseq.reverse_primer:
        cmd += f"--RV_primer {ampliseq.reverse_primer} "
    if not ampliseq.forward_primer and not ampliseq.reverse_primer:
        cmd += "--skip_cutadapt "
    if ampliseq.taxonomy == "dada":
        cmd += f"--dada_ref_taxonomy {ampliseq.dada_ref_taxonomy} "
    elif ampliseq.taxonomy == "qiime":
        cmd += f"--qiime_ref_taxonomy {ampliseq.qiime_ref_taxonomy} "
    elif ampliseq.taxonomy == "qiime_custom":
        cmd += (
            f"--classifier /datadrive/{ampliseq.container_name}/"
            f"{ampliseq.classifier.name} "
        )
    if ampliseq.exclude_taxa:
        cmd += f"--exclude_taxa {ampliseq.exclude_taxa} "

    # Add the cleanup commands to the end of the system call
    cmd += (
        f"; nextflow_status=$?; "
        f"nextflow clean -k -f || true; "
        f"mkdir -p "
        f"$AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/reports "
        f"$AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/results2; "
        f"rsync -a /datadrive/{ampliseq.container_name}/reports/ "
        f"$AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/reports/; "
        f"rsync -a /datadrive/{ampliseq.container_name}/results2/ "
        f"$AZ_BATCH_NODE_MOUNTS_DIR/{ampliseq.container_name}/results2/; "
        f"rm -rf /datadrive/{ampliseq.container_name}; "
        f"exit $nextflow_status"
    )

    return cmd


@shared_task
def run_ampliseq_batch(primary_key: int):
    """Submit an AmpliSeq request to Azure Batch.

    Args:
        primary_key: Primary key of the request to submit.

    """
    ampliseq = AmpliSeqRequest.objects.get(pk=primary_key)
    # Create the system call
    cmd = create_system_call(ampliseq=ampliseq)
    # Create a folder into which any error code files are to be written
    local_folder = os.path.join("olc_webportalv2", "media", str(ampliseq))
    os.makedirs(local_folder, exist_ok=True)

    # Submit the command to the AzureBatch service
    generic_api_submit(
        command=cmd,
        container_name=ampliseq.container_name,
        vm_size="Standard_D32s_v3",
        analysis_type="AmpliSeq",
        unique_id="FoodPort",
    )

    # Create an AmpliSeqAzureTask entry, so the progress of the analyses
    # can be followed by FoodPort
    AmpliSeqAzureTask.objects.create(
        ampliseq=ampliseq,
        exit_code_file=os.path.join(local_folder, "exit_codes.txt"),
    )


def check_for_task_completion(
    task: AmpliSeqAzureTask, batch_client: BatchServiceClient
) -> tuple[bool, AmpliSeqRequest]:
    """Check whether all Batch tasks for an AmpliSeq request have completed.

    Args:
        task: AmpliSeq task record being monitored.
        batch_client: Client used to query Azure Batch.

    Returns:
        Completion status and the corresponding AmpliSeq request.

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
            if cloudtask.state != "completed":
                tasks_completed = False
    # If something errors first time through, jobs can't get deleted. In that
    #   case, give up.
    except Exception as exc:
        AmpliSeqRequest.objects.filter(pk=task.ampliseq.pk).update(
            status="Error", errors=[str(exc)]
        )
        # Delete task, so we don't keep iterating over it.
        AmpliSeqAzureTask.objects.filter(id=task.id).delete()

    return tasks_completed, ampliseq_request


def delete_pool_job(
    batch_client: BatchServiceClient, batch_job_name: str
) -> bool:
    """Delete the job and pool after checking task exit codes.

    Args:
        batch_client: Client used to query and delete Batch resources.
        batch_job_name: Job and pool name.

    Returns:
        Whether all task exit codes were zero.

    """
    # Initialise the exit code status to True
    exit_codes_good = True
    # Iterate through the tasks associated with the name of the batch job
    for cloudtask in batch_client.task.list(batch_job_name):
        # The only 'good' exit code is 0
        if cloudtask.execution_info.exit_code != 0:
            # A non-zero code sets the boolean to False
            exit_codes_good = False
    # Release the Batch resources after checking task exit codes.
    batch_client.job.delete(job_id=batch_job_name)
    batch_client.pool.delete(pool_id=batch_job_name)
    return exit_codes_good


def create_sas_urls(ampliseq):
    """Create temporary download URLs for AmpliSeq reports.

    Args:
        ampliseq: AmpliSeq request whose reports are stored remotely.

    Returns:
        URLs for the execution report, trace, and timeline.

    """
    # Create a blob service client
    blob_service = get_storage_client()

    folder_path = (
        f"{ampliseq.container_name}/reports/{ampliseq.container_name}"
    )

    execution_report_sas_url = blob_service.create_transfer_url(
        ampliseq.container_name,
        "download",
        name=f"{folder_path}_execution_report.html",
        expires_in_seconds=8 * 24 * 60 * 60,
    )
    execution_trace_sas_url = blob_service.create_transfer_url(
        ampliseq.container_name,
        "download",
        name=f"{folder_path}_execution_trace.txt",
        expires_in_seconds=8 * 24 * 60 * 60,
    )
    execution_timeline_sas_url = blob_service.create_transfer_url(
        ampliseq.container_name,
        "download",
        name=f"{folder_path}_execution_timeline.html",
        expires_in_seconds=8 * 24 * 60 * 60,
    )
    return (
        execution_report_sas_url,
        execution_trace_sas_url,
        execution_timeline_sas_url,
    )


def download_reports(ampliseq: AmpliSeqRequest):
    """Download the execution report and timeline to the template directory.

    Args:
        ampliseq: AmpliSeq request whose reports should be downloaded.

    """
    local_folder = os.path.join("olc_webportalv2", "templates", "ampliseq")
    blob_service = get_storage_client()

    blob_service.download_file(
        ampliseq.container_name,
        f"{ampliseq.container_name}/reports/"
        f"{ampliseq.container_name}_execution_report.html",
        os.path.join(
            local_folder, f"{ampliseq.container_name}_execution_report.html"
        ),
    )
    blob_service.download_file(
        ampliseq.container_name,
        f"{ampliseq.container_name}/reports/"
        f"{ampliseq.container_name}_execution_timeline.html",
        os.path.join(
            local_folder, f"{ampliseq.container_name}_execution_timeline.html"
        ),
    )


def download_results(ampliseq: AmpliSeqRequest):
    """Package, upload, and link the results for an AmpliSeq request.

    Args:
        ampliseq: AmpliSeq request whose results should be downloaded.

    """
    local_folder = os.path.join("olc_webportalv2", "media", "ampliseq")
    results_folder = os.path.join(local_folder, ampliseq.container_name)
    results_dir = "results2"

    os.makedirs(results_folder, exist_ok=True)
    blob_service = get_storage_client()

    # List all blobs in the container
    blobs = blob_service.list_objects(ampliseq.container_name)

    # Boolean to track if any blobs were found for the results directory
    results_found = False

    # Iterate through the blobs and download those that are in the results
    # directory
    for blob in blobs:
        # Check if the blob is in the results directory
        if (
            "/".join(pathlib.Path(blob.name).parts[0:2])
            == f"{ampliseq.container_name}/{results_dir}"
        ):
            # Set the boolean to True since we found at least one result
            results_found = True

            # Create the appropriate subpath and ensure the directory exists
            file_name = pathlib.Path(blob.name).name
            subpath = "/".join(pathlib.Path(blob.name).parts[2:-1])
            path_to_create = os.path.join(results_folder, subpath)
            os.makedirs(path_to_create, exist_ok=True)

            # Download the blob to the appropriate path
            blob_service.download_file(
                ampliseq.container_name,
                blob.name,
                os.path.join(path_to_create, file_name),
            )

    # If no results were found, raise a FileNotFoundError
    if not results_found:
        raise FileNotFoundError(
            f"No results found in container {ampliseq.container_name} "
            f"under {ampliseq.container_name}/{results_dir}"
        )

    # Create a zip file of the results
    zip_path = os.path.join(local_folder, ampliseq.container_name + ".zip")
    shutil.make_archive(os.path.splitext(zip_path)[0], "zip", results_folder)

    # Upload the zip file to blob storage
    blob_service.upload_file(
        ampliseq.container_name, os.path.basename(zip_path), zip_path
    )

    sas_url = blob_service.create_transfer_url(
        ampliseq.container_name,
        "download",
        name=os.path.basename(zip_path),
        expires_in_seconds=730 * 24 * 60 * 60,
    )

    # Update the AmpliSeqRequest with the SAS URL for the results
    ampliseq.results_download_link = sas_url
    ampliseq.save()
    shutil.rmtree(results_folder)
    os.remove(zip_path)


def task_succeeded(ampliseq: AmpliSeqRequest):
    """Mark a request complete, publish its reports, and notify subscribers.

    Args:
        ampliseq: AmpliSeq request that completed successfully.

    """
    ampliseq.status = "Complete"
    # Create links to the report, trace, and timeline
    (
        ampliseq.execution_report_download_link,
        ampliseq.execution_trace_download_link,
        ampliseq.execution_timeline_download_link,
    ) = create_sas_urls(ampliseq=ampliseq)
    ampliseq.save()
    # Download the reports
    download_reports(ampliseq=ampliseq)
    download_results(ampliseq=ampliseq)
    # Send emails
    for email in ampliseq.emails_array:
        send_email(
            subject=f'AmpliSeq Analysis "{ampliseq.project_name}" Complete',
            body=f"Dear {ampliseq.user},\n"
            f'Your AmpliSeq analysis, "{ampliseq.project_name}", '
            "is complete.\n"
            f"The AmpliSeq analysis results are available "
            f"here: {ampliseq.results_download_link}.\n"
            f"The AmpliSeq report is available here: "
            f"{ampliseq.execution_report_download_link}.\n"
            f"The AmpliSeq trace is available here: "
            f"{ampliseq.execution_trace_download_link}.\n"
            f"The AmpliSeq timeline is available here: "
            f"{ampliseq.execution_timeline_download_link}\n\n"
            "Best regards,\n"
            "The FoodPort development team",
            recipient=email,
        )


def task_failed(ampliseq):
    """Mark a request failed and notify its subscribers.

    Args:
        ampliseq: AmpliSeq request that failed.

    """
    # Send emails
    errors = "\n".join(ampliseq.error_list) if ampliseq.error_list else "None"
    for email in ampliseq.emails_array:
        send_email(
            subject=f'AmpliSeq Analysis "{ampliseq.project_name}" Failed',
            body=f"Dear {ampliseq.user},\n"
            f'Your AmpliSeq analysis, "{ampliseq.project_name}", '
            "has failed.\n"
            f"The following errors were recorded: {errors}\n\n"
            "Sorry for the inconvenience,\n"
            "The FoodPort development team",
            recipient=email,
        )

    # Update the model with the error status
    ampliseq.status = "Error"
    ampliseq.save()


@shared_task
def check_ampliseq_tasks():
    """Check active tasks and handle completed AmpliSeq analyses."""
    # Create a batch client
    batch_client = create_batch_client()
    # Retrieve all AmpliSeqAzureTask objects (they should be deleted after
    # they finish, so anything retrieved should be active)
    ampliseq_tasks = AmpliSeqAzureTask.objects.filter()
    # Iterate over all the tasks to see if they are complete
    for task in ampliseq_tasks:
        task_completed, ampliseq = check_for_task_completion(
            task=task, batch_client=batch_client
        )
        # Allow the task to complete
        if not task_completed:
            continue

        # Clean up the job and pool if the task is complete
        exit_codes_good = delete_pool_job(
            batch_client=batch_client, batch_job_name=ampliseq.container_name
        )

        # Perform appropriate actions depending on whether or not the task was
        # successful
        if exit_codes_good:
            task_succeeded(ampliseq=ampliseq)
        else:
            task_failed(ampliseq=ampliseq)

        # Delete the AmpliSeqAzureTask
        AmpliSeqAzureTask.objects.filter(id=task.id).delete()
