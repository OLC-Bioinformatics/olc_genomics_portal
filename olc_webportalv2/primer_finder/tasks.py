#!/usr/bin/env python

"""
This module contains tasks for processing primer validation and verification
requests.
"""

# Standard imports
import datetime
import os
import posixpath
import shlex
import shutil
import time
from typing import (
    Any,
    Tuple
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
    VerifierPrimerSet
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


def _process_primer_request(*, panel_model: Any, request_model: Any, request_pk: int):
    """
    Process a primer validator or verifier request.

    Args:
        panel_model (Any): The model class for the panel.
        request_model (Any): The request model to process.
        request_pk (int): The primary key of the request object.
    """
    # Retrieve the primer model object corresponding to the
    # request primary key.
    primer_request = request_model.objects.get(pk=request_pk)

    # Upload the primer file to the Azure Blob request container.
    container_name, file_name = upload_primers(request=primer_request)

    input_blob_names = [file_name]

    # If a probe sequence is provided, upload it and add it to the list of
    # blobs that the Batch node must download.
    if getattr(primer_request, "probe_sequence", None):
        upload_probe(request=primer_request)
        input_blob_names.append("probe.fasta")

    # Handle the inclusivity panel files. This must happen before command
    # creation because any server-side benchmark copy must finish before its
    # SAS URL is generated and the Batch task is submitted.
    inclusivity_blob_names = _handle_panel_files(
        container_name=container_name,
        inclusivity=True,
        panel_model=panel_model,
        request_pk=request_pk,
    )
    input_blob_names.extend(inclusivity_blob_names)

    # Handle the exclusivity panel files.
    exclusivity_blob_names = _handle_panel_files(
        container_name=container_name,
        inclusivity=False,
        panel_model=panel_model,
        request_pk=request_pk,
    )
    input_blob_names.extend(exclusivity_blob_names)

    # Generate short-lived read-only SAS URLs for all task input files.
    # The Batch command uses curl to download these files directly to the
    # node's local /datadrive filesystem instead of reading them through
    # the BlobFuse mount.
    input_blobs = _generate_input_blob_sas_urls(
        container_name=container_name, blob_names=input_blob_names
    )

    # Set the path to the mounted container in the Azure Batch VM. The mount
    # remains in use for copying outputs back to Azure Blob Storage.
    path = "$AZ_BATCH_NODE_MOUNTS_DIR/{container}".format(container=container_name)

    # Create the PrimerValidator system call after all input blobs have been
    # prepared and their SAS URLs generated.
    cmd = _create_primer_cmd(
        container_name=container_name,
        file_name=file_name,
        input_blobs=input_blobs,
        path=path,
        request=primer_request,
    )

    # Submit the command to the Azure Batch service.
    generic_api_submit(
        command=cmd,
        container_name=container_name,
        input_file_pattern=None,
        vm_size="Standard_D4ds_v5",
        unique_id="FoodPort",
    )

    # Create the appropriate AzureRequest tracking row.
    if request_model == PrimerVerifierRequest:
        VerifierAzureRequest.objects.create(
            verifier_request=primer_request, exit_code_file="NA"
        )
    else:
        PrimerValidatorAzureRequest.objects.create(
            validator_request=primer_request, exit_code_file="NA"
        )


def _create_primer_cmd(
    *, container_name: str, file_name: str, input_blobs: list, path: str, request: Any
) -> str:
    """
    Create the system call.

    The call includes activating the conda environment, downloading input
    files directly from Azure Blob Storage to the node SSD with curl,
    creating files with the sequence IDs for each ZIP file, unzipping the ZIP
    files, running the primer_validator.py script, and copying the reports
    folder back to the container mount.

    Direct curl downloads are used for input files because very large files
    can fail when read through the BlobFuse mount even when their names,
    metadata, and sizes remain visible through the mounted filesystem.

    Args:
        container_name (str): Name of the request's Azure Blob container.
        file_name (str): Blob name of the uploaded primer file.
        input_blobs (list): Dictionaries containing blob names and read-only
            SAS URLs for the required task inputs.
        path (str): Path to the mounted request container on the Batch node.
        request (Any): Primer validator or verifier request object.

    Returns:
        str: The complete shell command submitted to Azure Batch.
    """
    # Fallback defaults for fields that may not exist on ValidatorRequest.
    mismatches = getattr(request, "mismatches", 2)
    min_amplicon_size = getattr(request, "min_amplicon_size", 0)
    max_amplicon_size = getattr(request, "max_amplicon_size", 1500)
    range_buffer = getattr(request, "range_buffer", 0)

    run_directory = "/datadrive/{run_name}".format(run_name=container_name)

    inclusivity_directory = posixpath.join(run_directory, "inclusivity")
    exclusivity_directory = posixpath.join(run_directory, "exclusivity")
    reports_directory = posixpath.join(run_directory, "reports")

    # Begin the command by activating the PrimerValidator environment and
    # enabling strict shell error handling.
    command_parts = [
        "source $CONDA/activate /envs/primer_validator",
        "set -euo pipefail",
        # Define the output mount in the shell so that the environment
        # variable is expanded on the Batch node.
        'mount_dir="{path}"'.format(path=path),
        # Remove local files from an earlier execution of this request.
        # This affects only the node's local SSD and does not delete blobs.
        "rm -rf {run_directory}".format(run_directory=shlex.quote(run_directory)),
        # Create local directories even when no inclusivity or exclusivity
        # panel was selected.
        "mkdir -p {inclusivity} {exclusivity} {reports}".format(
            inclusivity=shlex.quote(inclusivity_directory),
            exclusivity=shlex.quote(exclusivity_directory),
            reports=shlex.quote(reports_directory),
        ),
    ]

    # Download every required input blob directly to the node's local SSD.
    # shlex.quote() is essential because SAS URLs contain shell
    # metacharacters, including ampersands.
    for input_blob in input_blobs:
        blob_name = input_blob["blob_name"]
        sas_url = input_blob["sas_url"]

        destination_path = posixpath.join(run_directory, blob_name)
        destination_directory = posixpath.dirname(destination_path)
        partial_path = "{destination}.part".format(destination=destination_path)

        command_parts.extend(
            [
                "mkdir -p {directory}".format(
                    directory=shlex.quote(destination_directory)
                ),
                # Download to a temporary filename. Curl follows redirects,
                # fails on HTTP errors, and retries transient failures.
                (
                    "curl "
                    "--fail "
                    "--location "
                    "--retry 5 "
                    "--retry-delay 10 "
                    "--connect-timeout 60 "
                    "--continue-at - "
                    "--output {partial} "
                    "{source}"
                ).format(
                    partial=shlex.quote(partial_path), source=shlex.quote(sas_url)
                ),
                # Verify that curl produced a nonempty local file before moving
                # it to its final name.
                "test -s {partial}".format(partial=shlex.quote(partial_path)),
                # Rename the completed download to its final name.
                "mv {partial} {destination}".format(
                    partial=shlex.quote(partial_path),
                    destination=shlex.quote(destination_path),
                ),
                # Verify the final file as an additional safeguard.
                "test -s {destination}".format(
                    destination=shlex.quote(destination_path)
                ),
            ]
        )

    # Process inclusivity ZIP archives. The explicit existence test avoids
    # passing a literal glob pattern to unzip when the directory is empty.
    command_parts.append(
        (
            "for zip_file in {directory}/*.zip; do "
            '[ -e "$zip_file" ] || continue; '
            'archive_name=$(basename "$zip_file" .zip); '
            'unzip -tq "$zip_file"; '
            'unzip -n -q "$zip_file" -d {directory}; '
            'unzip -Z1 "$zip_file" '
            '| sed -n "s/\\.fasta$//p" '
            "| sort -u "
            '> {run_directory}/"${{archive_name}}"_seqids.txt; '
            "done"
        ).format(
            directory=shlex.quote(inclusivity_directory),
            run_directory=shlex.quote(run_directory),
        )
    )

    # Process exclusivity ZIP archives. The guarded loop allows the
    # exclusivity directory to be empty.
    command_parts.append(
        (
            "for zip_file in {directory}/*.zip; do "
            '[ -e "$zip_file" ] || continue; '
            'archive_name=$(basename "$zip_file" .zip); '
            'unzip -tq "$zip_file"; '
            'unzip -n -q "$zip_file" -d {directory}; '
            'unzip -Z1 "$zip_file" '
            '| sed -n "s/\\.fasta$//p" '
            "| sort -u "
            '> {run_directory}/"${{archive_name}}"_seqids.txt; '
            "done"
        ).format(
            directory=shlex.quote(exclusivity_directory),
            run_directory=shlex.quote(run_directory),
        )
    )

    # Create the PrimerValidator system call using the supplied arguments.
    primer_command = (
        "primer_validator.py "
        "-i {inclusivity} "
        "-e {exclusivity} "
        "-pf {primer_file_location} "
        "-r {reports} "
        "-m {mismatches} "
        "-min {min_amplicon_size} "
        "-max {max_amplicon_size} "
        "-rb {range_buffer}"
    ).format(
        inclusivity=shlex.quote(inclusivity_directory),
        exclusivity=shlex.quote(exclusivity_directory),
        primer_file_location=shlex.quote(posixpath.join(run_directory, file_name)),
        reports=shlex.quote(reports_directory),
        mismatches=shlex.quote(str(mismatches)),
        min_amplicon_size=shlex.quote(str(min_amplicon_size)),
        max_amplicon_size=shlex.quote(str(max_amplicon_size)),
        range_buffer=shlex.quote(str(range_buffer)),
    )

    # If contig breaks are to be permitted, add the flag to the command.
    if getattr(request, "contig_breaks", False):
        primer_command += " -cb"

    # Add the probe sequence. getattr() prevents AttributeError for request
    # types that do not define probe_sequence.
    if getattr(request, "probe_sequence", None):
        primer_command += " -p {probe_path}".format(
            probe_path=shlex.quote(posixpath.join(run_directory, "probe.fasta"))
        )

    command_parts.append(primer_command)

    # Copy the reports folder back to the BlobFuse-mounted request container.
    # Input files are no longer read through this mount, but the existing
    # output workflow is preserved.
    command_parts.append('rm -rf "$mount_dir/reports"')

    command_parts.append(
        'cp -R {reports} "$mount_dir/"'.format(reports=shlex.quote(reports_directory))
    )

    # Copy sequence-ID files back when at least one such file exists. The
    # guarded loop prevents cp from receiving an unmatched filename pattern
    # when no panel ZIP archives were selected.
    command_parts.append(
        (
            "for seqids_file in {run_directory}/*_seqids.txt; do "
            '[ -e "$seqids_file" ] || continue; '
            'cp "$seqids_file" "$mount_dir/"; '
            "done"
        ).format(run_directory=shlex.quote(run_directory))
    )

    # Join every operation so that the task stops at the first failed
    # download, archive validation, extraction, analysis, or result copy.
    return " && ".join(command_parts)


def _generate_input_blob_sas_urls(
    *, container_name: str, blob_names: list, expiry_hours: int = 12
) -> list:
    """
    Generate short-lived, read-only SAS URLs for Batch task input blobs.

    Each returned dictionary contains the source blob name and its complete
    read-only SAS URL. Blob-level SAS tokens are used because the Batch task
    only needs access to explicitly selected input files.

    Args:
        container_name (str): Name of the request's Azure Blob container.
        blob_names (list): Blob names to make available to the Batch task.
        expiry_hours (int): Number of hours for which the SAS URLs are valid.

    Returns:
        list: Dictionaries containing blob_name and sas_url values.

    Raises:
        FileNotFoundError: If one of the requested blobs does not exist.
    """
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )

    input_blobs = []
    unique_blob_names = []
    seen_blob_names = set()

    # Remove duplicate blob names while preserving their original order.
    # A set is used only for membership testing because dictionaries did not
    # guarantee insertion order in Python 3.5.
    for blob_name in blob_names:
        if blob_name not in seen_blob_names:
            seen_blob_names.add(blob_name)
            unique_blob_names.append(blob_name)

    for blob_name in unique_blob_names:
        if not blob_client.exists(
            container_name=container_name,
            blob_name=blob_name
        ):
            raise FileNotFoundError(
                "Required Batch input blob does not exist: "
                "{container}/{blob}".format(
                    container=container_name, blob=blob_name
                )
            )

        sas_token = blob_client.generate_blob_shared_access_signature(
            container_name=container_name,
            blob_name=blob_name,
            permission=BlobPermissions.READ,
            expiry=(
                datetime.datetime.utcnow() + datetime.timedelta(
                    hours=expiry_hours
                )
            ),
        )

        sas_url = blob_client.make_blob_url(
            container_name=container_name,
            blob_name=blob_name,
            sas_token=sas_token
        )

        input_blobs.append({"blob_name": blob_name, "sas_url": sas_url})

    return input_blobs


def _handle_panel_files(
    *,  # Force the use of keyword arguments
    container_name: str,
    inclusivity: bool,
    panel_model: Any,
    request_pk: int
) -> list:
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

    Returns:
        list: Blob names of the benchmark ZIP files required by the Batch
            task. The list is empty when no panel was selected.
    """
    # Determine the panel type
    panel_type = "inclusivity" if inclusivity else "exclusivity"

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

    benchmark_blob_names = []

    # Copy the benchmark files if the panel is specified
    if panel_filter.exists():
        for panel in panel_filter:
            benchmark_blob_name = copy_benchmark_files(
                container_name=container_name,
                genus=panel.genus.lower(),
                panel=panel_type,
            )
            benchmark_blob_names.append(benchmark_blob_name)
    else:
        create_empty_folder(container_name=container_name, panel=panel_type)

    return benchmark_blob_names


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


def _wait_for_blob_copy(
    *,
    blob_client: BlockBlobService,
    container_name: str,
    blob_name: str,
    timeout_seconds: int = 3600,
    poll_interval_seconds: int = 10
):
    """
    Wait for an asynchronous Azure Blob copy operation to complete.

    Azure Blob copy operations may complete asynchronously. This function
    polls the destination blob until the copy succeeds, fails, is aborted,
    or reaches the configured timeout.

    Args:
        blob_client (BlockBlobService): The Azure Blob service client.
        container_name (str): The destination container name.
        blob_name (str): The destination blob name.
        timeout_seconds (int): Maximum number of seconds to wait for the copy.
        poll_interval_seconds (int): Number of seconds between status checks.

    Raises:
        RuntimeError: If the copy operation fails or is aborted.
        TimeoutError: If the copy operation does not finish before timeout.
    """
    deadline = time.monotonic() + timeout_seconds

    while True:
        blob_properties = blob_client.get_blob_properties(
            container_name=container_name, blob_name=blob_name
        )

        copy_properties = blob_properties.properties.copy
        copy_status = getattr(copy_properties, "status", None)

        # A successful copy is ready for the Batch task to download.
        if copy_status == "success":
            return

        # Stop immediately if Azure reports a terminal failure.
        if copy_status in ("failed", "aborted"):
            status_description = getattr(
                copy_properties,
                "status_description",
                None
            )
            raise RuntimeError(
                "Azure Blob copy failed for {blob}: "
                "status={status}, description={description}".format(
                    blob=blob_name,
                    status=copy_status,
                    description=status_description
                )
            )

        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Timed out waiting for Azure Blob copy of {blob}; "
                "last copy status was {status}".format(
                    blob=blob_name, status=copy_status
                )
            )

        time.sleep(poll_interval_seconds)


def copy_benchmark_files(
    *,  # Force the use of keyword arguments
    container_name: str,
    genus: str,
    panel: str
) -> str:
    """
    Copy the benchmark files to the appropriate folder.

    If the destination blob already exists and has the same size as the
    benchmark source blob, the existing destination is reused. If an
    asynchronous copy is already pending, this function waits for it to
    complete. A failed, aborted, or mismatched destination is replaced.

    Args:
        container_name (str): The name of the container to which the benchmark
            files are to be copied.
        genus (str): The genus for which the benchmark files are to be copied.
        panel (str): The panel (inclusivity/exclusivity) for which the
            benchmark files are to be copied.

    Returns:
        str: The blob name of the benchmark archive in the request container.
    """
    # Create the blob service client for manipulating blobs
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )

    source_container_name = "benchmark-datasets"
    source_blob_name = "{genus}.zip".format(genus=genus)
    destination_blob_name = posixpath.join(panel, source_blob_name)

    # Retrieve the authoritative size of the benchmark source blob.
    source_properties = blob_client.get_blob_properties(
        container_name=source_container_name, blob_name=source_blob_name
    )
    source_size = source_properties.properties.content_length

    destination_exists = blob_client.exists(
        container_name=container_name, blob_name=destination_blob_name
    )

    if destination_exists:
        destination_properties = blob_client.get_blob_properties(
            container_name=container_name, blob_name=destination_blob_name
        )

        copy_properties = destination_properties.properties.copy
        copy_status = getattr(copy_properties, "status", None)

        # A previous invocation may already have started the copy. Wait for
        # that operation rather than starting another copy over the same blob.
        if copy_status == "pending":
            _wait_for_blob_copy(
                blob_client=blob_client,
                container_name=container_name,
                blob_name=destination_blob_name,
            )

            # Refresh the properties after the pending copy finishes.
            destination_properties = blob_client.get_blob_properties(
                container_name=container_name, blob_name=destination_blob_name
            )
            copy_properties = destination_properties.properties.copy
            copy_status = getattr(copy_properties, "status", None)

        destination_size = destination_properties.properties.content_length

        # A missing copy status is acceptable because the destination may have
        # been uploaded directly instead of being created by Copy Blob.
        if copy_status in (None, "success") and destination_size == source_size:
            return destination_blob_name

        # The destination is failed, aborted, incomplete, or does not match
        # the source. Remove it before starting a replacement copy.
        blob_client.delete_blob(
            container_name=container_name, blob_name=destination_blob_name
        )

    # Generate a short-lived read-only SAS URL for the benchmark source.
    # This allows Copy Blob to read the source even when the benchmark
    # container is private.
    source_sas_token = blob_client.generate_blob_shared_access_signature(
        container_name=source_container_name,
        blob_name=source_blob_name,
        permission=BlobPermissions.READ,
        expiry=(datetime.datetime.utcnow() + datetime.timedelta(hours=12)),
    )

    source_url = blob_client.make_blob_url(
        container_name=source_container_name,
        blob_name=source_blob_name,
        sas_token=source_sas_token,
    )

    # Copy the benchmark file into the request-specific container.
    blob_client.copy_blob(
        container_name=container_name,
        blob_name=destination_blob_name,
        copy_source=source_url,
    )

    # Copy Blob may be asynchronous, particularly for a large archive.
    _wait_for_blob_copy(
        blob_client=blob_client,
        container_name=container_name,
        blob_name=destination_blob_name,
    )

    # Perform a final size check after Azure reports a successful copy.
    destination_properties = blob_client.get_blob_properties(
        container_name=container_name, blob_name=destination_blob_name
    )
    destination_size = destination_properties.properties.content_length

    if destination_size != source_size:
        raise RuntimeError(
            "Benchmark copy size mismatch for {blob}: "
            "source={source_size}, destination={destination_size}".format(
                blob=destination_blob_name,
                source_size=source_size,
                destination_size=destination_size,
            )
        )

    return destination_blob_name


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
