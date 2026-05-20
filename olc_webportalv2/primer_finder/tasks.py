#!/usr/bin/env python

"""
This module contains tasks for processing primer validation and verification
requests.
"""

# Standard imports
import datetime
import shutil
import os
from typing import (
    Any,
    Tuple,
)

# Django imports
from django.conf import settings

# Third-party imports
import azure.batch.batch_service_client as batch
from azure.storage.blob import BlockBlobService
from azure.storage.blob import BlobPermissions
from azure.batch import batch_auth
import azure.batch.models as batchmodels
from celery import shared_task

# Portal-specific imports
from olc_webportalv2.common.methods import generic_api_submit
from olc_webportalv2.primer_finder.methods import (
    _archive_reports_and_upload,
    create_details,
    populate_report,
    send_email,
    upload_primers,
    upload_probe,
    _upload_summary_excel
)

from olc_webportalv2.primer_finder.models import (
    PrimerValidatorAzureRequest,
    PrimerVerifierRequest,
    ValidatorPanel,
    ValidatorPrimerSet,
    ValidatorRequest,
    VerifierAzureRequest,
    VerifierPanel,
    VerifierPrimerSet,
)


@shared_task
def run_primer_verifier(
    *,  # Force the use of keyword arguments
    verifier_request_pk: int
):
    """
    Run the PrimerVerifier analysis.

    Args:
        verifier_request_pk (int): The primary key of the
            PrimerVerifierRequest object for which the analysis is to be run.
    """
    try:
        _process_primer_request(
            panel_model=VerifierPanel,
            request_model=PrimerVerifierRequest,
            request_pk=verifier_request_pk
        )
    except Exception as exc:
        _handle_primer_error(
            exception=exc,
            request_model=PrimerVerifierRequest,
            request_pk=verifier_request_pk
        )


def _process_primer_request(
    *,  # Force the use of keyword arguments
    panel_model: Any,
    request_model: Any,
    request_pk: int
):
    """
    Process a primer (validator or verifier) request.

    Args:
        panel_model (Any): The model class for the panel.
        request_model (Any): The request model to process.
        request_pk (int): The primary key of the request object.
    """
    # Retrieve the primer model object corresponding to the
    # request primary key
    primer_request = request_model.objects.get(
        pk=request_pk
    )

    # Upload the primer file to the AzureBatch VM
    container_name, file_name = upload_primers(request=primer_request)

    # If a probe sequence is provided, upload it
    if getattr(primer_request, 'probe_sequence', None):
        upload_probe(request=primer_request)

    # Set the path to the mounted container in the AzureBatch VM
    path = '$AZ_BATCH_NODE_MOUNTS_DIR/{container}'.format(
        container=container_name
    )

    # Create the PrimerValidator system call
    cmd = _create_primer_cmd(
        container_name=container_name,
        file_name=file_name,
        path=path,
        request=primer_request
    )

    # Handle the inclusivity panel files
    _handle_panel_files(
        container_name=container_name,
        inclusivity=True,
        panel_model=panel_model,
        request_pk=request_pk
    )

    # Handle the exclusivity panel files
    _handle_panel_files(
        container_name=container_name,
        inclusivity=False,
        panel_model=panel_model,
        request_pk=request_pk
    )

    # Submit the command to the AzureBatch service
    generic_api_submit(
        command=cmd,
        container_name=container_name,
        input_file_pattern=None,
        vm_size='Standard_D4ds_v5',
        unique_id='FoodPort'
    )

    # Create the appropriate AzureRequest tracking row
    if request_model == PrimerVerifierRequest:
        VerifierAzureRequest.objects.create(
            verifier_request=primer_request, exit_code_file="NA"
        )
    else:
        PrimerValidatorAzureRequest.objects.create(
            validator_request=primer_request, exit_code_file="NA"
        )


def _create_primer_cmd(
    *,  # Force the use of keyword arguments
    container_name: str,
    file_name: str,
    path: str,
    request: Any
) -> str:
    """
    Create the system call. The call includes activating the
    conda environment, copying the input files to a SSD, creating files with
    the sequence IDs for each zip file, unzipping the zip files, running the
    primer_validator.py script, and copying the reports folder back to the
    container mount.
    """
    # fallback defaults for fields that may not exist on ValidatorRequest
    mismatches = getattr(request, "mismatches", 2)
    min_amplicon_size = getattr(request, "min_amplicon_size", 0)
    max_amplicon_size = getattr(request, "max_amplicon_size", 1500)
    range_buffer = getattr(request, "range_buffer", 0)

    # Create the PrimerValidator system call using the supplied arguments
    cmd = (
        "source $CONDA/activate /envs/primer_validator && "
        "set -euo pipefail; "
        "mkdir -p /datadrive/{run_name}/inclusivity /datadrive/{run_name}/exclusivity && "
        "cp -R {path} /datadrive/ && "
        "shopt -s nullglob && "
        # Inclusivity archives
        "for zip_file in /datadrive/{run_name}/inclusivity/*.zip; do "
        'archive_name=$(basename "$zip_file" .zip); '
        'unzip -n -q "$zip_file" -d /datadrive/{run_name}/inclusivity || true; '
        # List .fasta members, keep full path minus .fasta
        'unzip -Z1 "$zip_file" | grep -E "\\.fasta$" | sed "s/\\.fasta$//" '
        "| sort -u > /datadrive/{run_name}/${{archive_name}}_seqids.txt; "
        "done; "
        # Exclusivity archives
        "for zip_file in /datadrive/{run_name}/exclusivity/*.zip; do "
        'archive_name=$(basename "$zip_file" .zip); '
        'unzip -n -q "$zip_file" -d /datadrive/{run_name}/exclusivity || true; '
        'unzip -Z1 "$zip_file" | grep -E "\\.fasta$" | sed "s/\\.fasta$//" '
        "| sort -u > /datadrive/{run_name}/${{archive_name}}_seqids.txt; "
        "done; "
        # Run validator
        "primer_validator.py "
        "-i /datadrive/{run_name}/inclusivity "
        "-e /datadrive/{run_name}/exclusivity "
        "-pf /datadrive/{run_name}/{primer_file_location} "
        "-r /datadrive/{run_name}/reports "
        "-m {mismatches} "
        "-min {min_amplicon_size} "
        "-max {max_amplicon_size} "
        "-rb {range_buffer} "
    ).format(
        run_name=container_name,
        path=path,
        primer_file_location=file_name,
        mismatches=mismatches,
        min_amplicon_size=min_amplicon_size,
        max_amplicon_size=max_amplicon_size,
        range_buffer=range_buffer,
    )

    # If contig breaks are to be permitted, add the flag to the command
    if getattr(request, "contig_breaks", False):
        cmd += "-cb "

    # Add the probe sequence (safe: getattr prevents AttributeError)
    if getattr(request, "probe_sequence", None):
        cmd += " -p /datadrive/{run_name}/probe.fasta".format(
            run_name=container_name,
        )

    # Copy the reports folder and seqids files to the container mount
    cmd += (
        " && cp -R /datadrive/{run_name}/reports {path} && "
        "cp /datadrive/{run_name}/*_seqids.txt {path}".format(
            run_name=container_name, path=path
        )
    )
    return cmd


def _handle_panel_files(
    *,  # Force the use of keyword arguments
    container_name: str,
    inclusivity: bool,
    panel_model: Any,
    request_pk: int
):
    """
    Handle inclusivity or exclusivity panel files. If the panel is specified,
    copy the benchmark files to the appropriate folder. If the panel is not
    specified, create an empty folder.

    Args:
        container_name (str): Name of the container.
        inclusivity (bool): True if handling inclusivity panel, False for
            exclusivity.
        panel_model (Any): The model class for the panel.
        request_pk (int): Primary key of the request.

    """
    # Determine the panel type
    panel_type = 'inclusivity' if inclusivity else 'exclusivity'

    # Choose the correct FK field name depending on panel_model
    if panel_model == VerifierPanel:
        fk_name = "verifier_request_id"
    else:
        fk_name = "validator_request_id"

    # Run queries to retrieve all objects corresponding to the appropriate
    # panel
    panel_filter = panel_model.objects.filter(
        **{fk_name: request_pk, "panel": panel_type}
    )

    # Copy the benchmark files if the panel is specified
    if panel_filter:
        for panel in panel_filter:
            copy_benchmark_files(
                container_name=container_name,
                genus=panel.genus.lower(),
                panel=panel_type
            )
    else:
        create_empty_folder(
            container_name=container_name,
            panel=panel_type
        )


def _handle_primer_error(
    *,  # Force the use of keyword arguments
    exception: Exception,
    request_model: Any,
    request_pk: int
):
    """
    Handle errors during PrimerVerifier analysis.

    Args:
        exc (Exception): The exception that occurred.
        request_model (Any): The request model instance.
        request_pk (int): Primary key of the request.
    """
    # Retrieve the request object corresponding to the
    # request primary key
    request = request_model.objects.get(
        pk=request_pk
    )

    # Update the model with the error status
    request.status = 'Error'
    request.errors = str(exception)
    request.save()


def copy_benchmark_files(
    *,  # Force the use of keyword arguments
    container_name: str,
    genus: str,
    panel: str
):
    """
    Copy the benchmark files to the appropriate folder

    Args:
        container_name (str): The name of the container to which the benchmark
            files are to be copied
        genus (str): The genus for which the benchmark files are to be copied
        panel (str): The panel (inclusivity/exclusivity) for which the
            benchmark files are to be copied
    """
    # Create the blob service client for manipulating blobs
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Copy the benchmark files to the appropriate folder
    blob_client.copy_blob(
        container_name=container_name,
        blob_name=os.path.join(panel, '{genus}.zip'.format(genus=genus)),
        copy_source='https://{account}.blob.core.windows.net/'
        'benchmark-datasets/{genus}.zip'.format(
            account=settings.AZURE_ACCOUNT_NAME,
            genus=genus
        )
    )


def create_empty_folder(
    *,  # Force the use of keyword arguments
    container_name: str,
    panel: str
):
    """
    Create an empty folder in the specified container

    Args:
        container_name (str): The name of the container in which the empty
            folder is to be created
        panel (str): The panel (inclusivity/exclusivity) for which the empty
            folder is to be created
    """
    # Create the blob service client for manipulating blobs
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    # Create the empty folder
    blob_client.create_blob_from_text(
        container_name=container_name,
        blob_name=os.path.join(panel, 'empty.txt'),
        text=''
    )


def check_verifier_tasks():
    """
    Check the status of tasks. If task fails, perform clean-up. If task
    succeeds, perform necessary steps and clean-up.
    """
    # Retrieve all VerifierAzureRequest objects
    verifier_tasks = VerifierAzureRequest.objects.filter()

    # Extract the credentials from the settings
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )

    # Create the batch client for manipulating batch jobs, and pools
    batch_client = batch.BatchServiceClient(
        credentials,
        base_url=settings.BATCH_ACCOUNT_URL
    )

    for task in verifier_tasks:
        _process_verifier_task(
            batch_client=batch_client,
            task=task
        )


def _process_verifier_task(
    *,  # Force the use of keyword arguments
    batch_client: batch.BatchServiceClient,
    task: VerifierAzureRequest
):
    """
    Process an individual verifier task.

    Args:
        batch_client (batch.BatchServiceClient): The Azure Batch client.
        task (VerifierAzureRequest): The task to process.
    """
    try:
        verifier_request = PrimerVerifierRequest.objects.get(
            pk=task.verifier_request.pk
        )
        batch_job_name = verifier_request.container_namer()
        tasks_completed = _check_tasks_completed(
            batch_client=batch_client,
            batch_job_name=batch_job_name
        )

        if tasks_completed:
            exit_codes_good = _check_exit_codes(
                batch_client=batch_client,
                batch_job_name=batch_job_name
            )
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)

            if exit_codes_good:
                _handle_successful_verifier_run(
                    batch_job_name=batch_job_name,
                    verifier_request=verifier_request
                )
            else:
                _handle_failed_verifier_run(verifier_request=verifier_request)

            VerifierAzureRequest.objects.filter(id=task.id).delete()

    except Exception as exc:
        PrimerVerifierRequest.objects.filter(
            pk=task.verifier_request.pk
        ).update(status='Error', errors=exc)
        VerifierAzureRequest.objects.filter(id=task.id).delete()


def _check_tasks_completed(
    *,  # Force the use of keyword arguments
    batch_client: batch.BatchServiceClient,
    batch_job_name: str
) -> bool:
    """
    Check if all tasks in the batch job have completed.

    Args:
        batch_client (batch.BatchServiceClient): The Azure Batch client.
        batch_job_name (str): The name of the batch job.

    Returns:
        bool: True if all tasks have completed, False otherwise.
    """
    try:
        for cloud_task in batch_client.task.list(batch_job_name):
            if cloud_task.state != batchmodels.TaskState.completed:
                return False
        return True
    except Exception:
        return False


def _check_exit_codes(
    *,  # Force the use of keyword arguments
    batch_client: batch.BatchServiceClient,
    batch_job_name: str
) -> bool:
    """
    Check if all tasks in the batch job have an exit code of 0.

    Args:
        batch_client (batch.BatchServiceClient): The Azure Batch client.
        batch_job_name (str): The name of the batch job.

    Returns:
        bool: True if all exit codes are 0, False otherwise.
    """
    # Iterate through the tasks associated with the name of the batch job
    for cloud_task in batch_client.task.list(batch_job_name):

        # Check if the exit code is 0
        if cloud_task.execution_info.exit_code != 0:
            return False
    return True


def _handle_successful_verifier_run(
    *,  # Force the use of keyword arguments
    batch_job_name: str,
    verifier_request: PrimerVerifierRequest
):
    """
    Handle a successful PrimerVerifier run.

    Args:
        batch_job_name (str): The name of the batch job.
        verifier_request (PrimerVerifierRequest): The request object.
    """
    report_sas_url, summary_sas_url, run_folder = post_verifier_run(
        batch_job_name=batch_job_name,
        verifier_request=verifier_request
    )
    email_list = verifier_request.emails_array
    for email in email_list:
        subject = 'PrimerVerifier Analysis "{name}" Complete'.format(
            name=str(verifier_request.project_name)
        )
        body = (
            'Dear {user},\n'
            'Your PrimerVerifier analysis, "{name}", is complete.\n'
            'Raw PrimerVerifier outputs are available here: {report_url}.\n\n'
            'Summarised outputs are available here: {summary_url}.\n\n'
            'Best regards,\n'
            'The FoodPort development team'
            .format(
                user=verifier_request.user,
                name=str(verifier_request.project_name),
                report_url=report_sas_url,
                summary_url=summary_sas_url
            )
        )

        # Send the email
        send_email(subject=subject, body=body, recipient=email)
    if verifier_request.report:
        shutil.rmtree(run_folder)


def _handle_failed_verifier_run(
    *,  # Force the use of keyword arguments
    verifier_request: PrimerVerifierRequest
):
    """
    Handle a failed PrimerVerifier run.

    Args:
        verifier_request (PrimerVerifierRequest): The request object.
    """
    # Extract the email list from the request
    email_list = verifier_request.emails_array
    for email in email_list:
        subject = 'PrimerVerifier Analysis "{name}" Failed'.format(
            name=str(verifier_request.project_name)
        )
        body = (
            'Dear {user},\n'
            'Your PrimerVerifier analysis, "{name}", has failed.\n'
            'Sorry for the inconvenience,\n'
            'The FoodPort development team'
            .format(
                user=verifier_request.user,
                name=str(verifier_request.project_name)
            )
        )

        # Send the email
        send_email(subject=subject, body=body, recipient=email)
    verifier_request.status = 'Error'
    verifier_request.save()


def post_verifier_run(
    *,  # Force the use of keyword arguments
    batch_job_name: str,
    verifier_request: PrimerVerifierRequest
) -> Tuple[str, str, str]:
    """
    Post-run steps for the PrimerVerifier analysis.

    Args:
        batch_job_name (str): The name of the batch job.
        verifier_request (PrimerVerifierRequest): The PrimerVerifierRequest
            object corresponding to the analysis.

    Returns:
        Tuple[str, str, str]: A tuple containing the SAS URLs for the
            PrimerVerifier report, the summary report, and the folder
            containing the reports.
    """
    try:
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )

        # Delete .fasta and .zip files from the container
        _cleanup_blob_container(
            blob_client=blob_client,
            container_name=batch_job_name
        )

        # Download the blob container, and store the JSON-formatted report
        # into the model
        verifier_request, report_folder = populate_report(
            request=verifier_request
        )
        print('Report folder:', report_folder)

        # Create a JSON-formatted summary report
        verifier_request = create_details(
            request=verifier_request,
            primer_set_model=VerifierPrimerSet,
            panel_model=VerifierPanel,
        )

        # Generate the Excel summary and upload it next to the JSON reports
        _upload_summary_excel(
            blob_client=blob_client,
            container_name=batch_job_name,
            request=verifier_request,
        )

        # Archive reports/ and upload the ZIP to the container root
        _archive_reports_and_upload(
            blob_client=blob_client,
            container_name=batch_job_name
        )

        # Generate SAS URLs (Excel summary, archived reports ZIP)
        excel_sas_url, archive_sas_url = _generate_sas_urls(
            blob_client=blob_client,
            container_name=batch_job_name,
        )

        # Update the model with the SAS URLs
        verifier_request.report_download_link = archive_sas_url
        verifier_request.summary_download_link = excel_sas_url
        verifier_request.status = 'Complete'
        verifier_request.save()

        return archive_sas_url, excel_sas_url, report_folder

    except Exception as exc:
        print(
            'Error in post_verifier_run for %s: %s',
            batch_job_name,
            str(exc),
        )
        raise


def _cleanup_blob_container(
    *,  # Force the use of keyword arguments
    blob_client: BlockBlobService,
    container_name: str
):
    """
    Delete .fasta and .zip files from the blob container.

    Args:
        blob_client (BlockBlobService): The Azure Blob service client.
        container_name (str): The name of the container.
    """
    # Iterate through the blobs in the container
    for blob in blob_client.list_blobs(container_name=container_name):
        if blob.name.endswith(('.fasta', '.zip')):
            blob_client.delete_blob(
                container_name=container_name,
                blob_name=blob.name
            )


def _generate_sas_urls(
    *,  # Force the use of keyword arguments
    blob_client: BlockBlobService,
    container_name: str
) -> Tuple[str, str]:
    """
    Generate SAS URLs for the report and summary files.

    Args:
        blob_client (BlockBlobService): The Azure Blob service client.
        container_name (str): The name of the container.

    Returns:
        Tuple[str, str]: The report and summary SAS URLs.
    """
    # Generate an SAS token with read access
    sas_token = blob_client.generate_container_shared_access_signature(
        container_name=container_name,
        permission=BlobPermissions.READ,
        expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8)
    )

    # Create SAS URLs for the Excel summary report archive of reports
    excel_blob_name = "reports/{name}_summary_report.xlsx".format(
        name=container_name
    )
    archive_blob_name = "{name}_reports.zip".format(
        name=container_name
    )

    excel_sas_url = blob_client.make_blob_url(
        container_name=container_name,
        blob_name=excel_blob_name,
        sas_token=sas_token
    )
    archive_sas_url = blob_client.make_blob_url(
        container_name=container_name,
        blob_name=archive_blob_name,
        sas_token=sas_token
    )
    return excel_sas_url, archive_sas_url


@shared_task
def run_primer_validator(
    *,  # Force the use of keyword arguments
    validator_request_pk: int
):
    """
    Run the PrimerValidator analysis.

    Args:
        validator_request_pk (int): The primary key of the
            ValidatorRequest object for which the analysis is to be run.
    """
    try:
        _process_primer_request(
            panel_model=ValidatorPanel,
            request_model=ValidatorRequest,
            request_pk=validator_request_pk
        )
    except Exception as exc:
        _handle_primer_error(
            exception=exc,
            request_model=ValidatorRequest,
            request_pk=validator_request_pk,
        )


def check_validator_tasks():
    """
    Check the status of ValidatorAzureRequest tasks. If a task fails, perform
    clean-up. If a task succeeds, perform necessary steps and clean-up.
    """
    # Get all active validator tasks
    validator_tasks = PrimerValidatorAzureRequest.objects.filter()

    # Set up the credentials
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )

    # Create the batch client
    batch_client = batch.BatchServiceClient(
        credentials, base_url=settings.BATCH_ACCOUNT_URL
    )

    # Process each validator task
    for task in validator_tasks:
        _process_validator_task(
            batch_client=batch_client,
            task=task
        )


def _process_validator_task(
    *,  # Force the use of keyword arguments
    batch_client: batch.BatchServiceClient,
    task: PrimerValidatorAzureRequest
):
    """
    Process an individual validator task.

    Args:
        batch_client (batch.BatchServiceClient): The Azure Batch client.
        task (PrimerValidatorAzureRequest): The task to process.
    """
    try:
        # Fetch the associated ValidatorRequest
        validator_request = ValidatorRequest.objects.get(
            pk=task.validator_request.pk
        )

        # Get the batch job name
        batch_job_name = validator_request.container_namer()

        # Check if all tasks in the batch job are completed
        tasks_completed = _check_tasks_completed(
            batch_client=batch_client, batch_job_name=batch_job_name
        )

        if tasks_completed:
            exit_codes_good = _check_exit_codes(
                batch_client=batch_client, batch_job_name=batch_job_name
            )
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)

            if exit_codes_good:
                _handle_successful_validator_run(
                    batch_job_name=batch_job_name,
                    validator_request=validator_request
                )
            else:
                _handle_failed_validator_run(
                    validator_request=validator_request
                )

            PrimerValidatorAzureRequest.objects.filter(id=task.id).delete()

    except Exception as exc:
        ValidatorRequest.objects.filter(pk=task.validator_request.pk).update(
            status="Error", errors=exc
        )
        PrimerValidatorAzureRequest.objects.filter(id=task.id).delete()


def _handle_successful_validator_run(
    *,  # Force the use of keyword arguments
    batch_job_name: str,
    validator_request: ValidatorRequest
):
    """
    Handle a successful PrimerValidator run.
    """
    report_sas_url, summary_sas_url, run_folder = post_validator_run(
        batch_job_name=batch_job_name, validator_request=validator_request
    )
    email_list = validator_request.emails_array or []
    for email in email_list:
        subject = 'PrimerValidator Analysis "{name}" Complete'.format(
            name=str(validator_request.project_name)
        )
        body = (
            "Dear {user},\n"
            'Your PrimerValidator analysis, "{name}", is complete.\n'
            "Raw PrimerValidator outputs are available here: {report_url}.\n\n"
            "Summarised outputs are available here: {summary_url}.\n\n"
            "Best regards,\n"
            "The FoodPort development team".format(
                user=validator_request.user,
                name=str(validator_request.project_name),
                report_url=report_sas_url,
                summary_url=summary_sas_url,
            )
        )
        send_email(subject=subject, body=body, recipient=email)
    if validator_request.report:
        shutil.rmtree(run_folder)


def _handle_failed_validator_run(
    *,  # Force the use of keyword arguments
    validator_request: ValidatorRequest
):
    """
    Handle a failed PrimerValidator run.
    """
    email_list = validator_request.emails_array or []
    for email in email_list:
        subject = 'PrimerValidator Analysis "{name}" Failed'.format(
            name=str(validator_request.project_name)
        )
        body = (
            "Dear {user},\n"
            'Your PrimerValidator analysis, "{name}", has failed.\n'
            "Sorry for the inconvenience,\n"
            "The FoodPort development team".format(
                user=validator_request.user,
                name=str(validator_request.project_name)
            )
        )
        send_email(subject=subject, body=body, recipient=email)
    validator_request.status = "Error"
    validator_request.save()


def post_validator_run(
    *,  # Force the use of keyword arguments
    batch_job_name: str,
    validator_request: ValidatorRequest
) -> Tuple[str, str, str]:
    """
    Post-run steps for the PrimerValidator analysis.
    """
    try:
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY,
        )

        # Remove the .fasta and .zip files from the container
        _cleanup_blob_container(
            blob_client=blob_client,
            container_name=batch_job_name
        )

        # Download the blob container, and store the JSON-formatted report
        # into the model
        validator_request, report_folder = populate_report(
            request=validator_request
        )

        # Create the details for the validator request
        validator_request = create_details(
            request=validator_request,
            primer_set_model=ValidatorPrimerSet,
            panel_model=ValidatorPanel,
            analysis="validator"
        )

        # Generate the Excel summary and upload it next to the JSON reports
        _upload_summary_excel(
            blob_client=blob_client,
            container_name=batch_job_name,
            request=validator_request,
        )

        # Archive reports/ and upload the ZIP to the container root
        _archive_reports_and_upload(
            blob_client=blob_client, container_name=batch_job_name
        )

        # SAS URLs (Excel summary, archived reports ZIP)
        excel_sas_url, archive_sas_url = _generate_sas_urls(
            blob_client=blob_client,
            container_name=batch_job_name,
        )
        validator_request.report_download_link = archive_sas_url
        validator_request.summary_download_link = excel_sas_url
        validator_request.status = "Complete"
        validator_request.save()

        return archive_sas_url, excel_sas_url, report_folder
    except Exception as exc:
        print(
            "Error in post_validator_run for %s: %s",
            batch_job_name,
            str(exc),
        )
        raise
