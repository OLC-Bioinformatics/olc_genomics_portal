#!/usr/bin/env python

"""
This module contains tasks for processing primer validation and verification
requests.
"""

# Standard imports
import os
import posixpath
import shlex
import shutil
import csv
import json
from io import StringIO
import logging
from typing import (
    Any,
    Tuple
)

# Django imports
from django.conf import settings

# Third-party imports
from Bio import SeqIO
from celery import shared_task

# Portal-specific imports
from olc_webportalv2.common.methods import (
    create_batch_client,
    generic_api_submit
)
from olc_webportalv2.common.benchmarks import (
    benchmark_archive_info,
    select_benchmark_blob,
    validate_benchmark_panel,
)
from olc_webportalv2.primer_finder.methods import (
    _archive_reports_and_upload,
    _validator_probe_records,
    create_details,
    populate_report,
    send_email,
    upload_primers,
    upload_probe,
    _upload_summary_excel
)
from olc_webportalv2.primer_finder.validator_gene_reports import (
    categorize_gene_hit_details,
    categorize_gene_hits,
    filter_report_by_gene_categories,
    gene_exclusion_note,
)

from olc_webportalv2.primer_finder.models import (
    PrimerValidatorAzureRequest,
    PrimerVerifierRequest,
    ValidatorPanel,
    ValidatorAnalysisTask,
    ValidatorPrimerSet,
    ValidatorRequest,
    ValidatorTarget,
    VerifierAzureRequest,
    VerifierPanel,
    VerifierPrimerSet
)
from olc_webportalv2.common.storage_client import get_storage_client
from olc_webportalv2.common.batch_client import BatchServiceError
from olc_webportalv2.common.batch_recovery import batch_pool_id_from_job_id

logger = logging.getLogger(__name__)

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
            request_model=PrimerVerifierRequest,
            request_pk=verifier_request_pk
        )
    except Exception as exc:
        _handle_primer_error(
            exception=exc,
            request_model=PrimerVerifierRequest,
            request_pk=verifier_request_pk
        )


def _process_primer_request(*, request_model: Any, request_pk: int):
    """
    Process a primer validator or verifier request.

    Args:
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

    # Handle the inclusivity panel files
    inclusivity_blob_names = _handle_panel_files(
        container_name=container_name,
        inclusivity=True,
        request=primer_request,
    )
    input_blob_names.extend(inclusivity_blob_names)

    # Handle the exclusivity panel files.
    exclusivity_blob_names = _handle_panel_files(
        container_name=container_name,
        inclusivity=False,
        request=primer_request,
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
    path = f"$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}"

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

    run_directory = f"/datadrive/{container_name}"

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
        f'mount_dir="{path}"',
        # Remove local files from an earlier execution of this request.
        # This affects only the node's local SSD and does not delete blobs.
        f"rm -rf {shlex.quote(run_directory)}",
        # Create local directories even when no inclusivity or exclusivity
        # panel was selected.
        f"mkdir -p {shlex.quote(inclusivity_directory)} {shlex.quote(exclusivity_directory)} {shlex.quote(reports_directory)}",
    ]

    # Download every required input blob directly to the node's local SSD.
    # shlex.quote() is essential because SAS URLs contain shell
    # metacharacters, including ampersands.
    for input_blob in input_blobs:
        blob_name = input_blob["blob_name"]
        sas_url = input_blob["sas_url"]

        destination_path = posixpath.join(run_directory, blob_name)
        destination_directory = posixpath.dirname(destination_path)
        partial_path = f"{destination_path}.part"

        command_parts.extend(
            [
                f"mkdir -p {shlex.quote(destination_directory)}",
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
                    f"--output {shlex.quote(partial_path)} "
                    f"{shlex.quote(sas_url)}"
                ),
                # Verify that curl produced a nonempty local file before moving
                # it to its final name.
                f"test -s {shlex.quote(partial_path)}",
                # Rename the completed download to its final name.
                f"mv {shlex.quote(partial_path)} {shlex.quote(destination_path)}",
                # Verify the final file as an additional safeguard.
                f"test -s {shlex.quote(destination_path)}",
            ]
        )

    # Process inclusivity ZIP archives. The explicit existence test avoids
    # passing a literal glob pattern to unzip when the directory is empty.
    command_parts.append(
        (
            f"for zip_file in {shlex.quote(inclusivity_directory)}/*.zip; do "
            '[ -e "$zip_file" ] || continue; '
            'archive_name=$(basename "$zip_file" .zip); '
            'unzip -tq "$zip_file"; '
            f'unzip -n -q "$zip_file" -d {shlex.quote(inclusivity_directory)}; '
            'unzip -Z1 "$zip_file" '
            '| sed -n "s/\\.fasta$//p" '
            "| sort -u "
            f'> {shlex.quote(run_directory)}/"${{archive_name}}"_seqids.txt; '
            "done"
        )
    )

    # Process exclusivity ZIP archives. The guarded loop allows the
    # exclusivity directory to be empty.
    command_parts.append(
        (
            f"for zip_file in {shlex.quote(exclusivity_directory)}/*.zip; do "
            '[ -e "$zip_file" ] || continue; '
            'archive_name=$(basename "$zip_file" .zip); '
            'unzip -tq "$zip_file"; '
            f'unzip -n -q "$zip_file" -d {shlex.quote(exclusivity_directory)}; '
            'unzip -Z1 "$zip_file" '
            '| sed -n "s/\\.fasta$//p" '
            "| sort -u "
            f'> {shlex.quote(run_directory)}/"${{archive_name}}"_seqids.txt; '
            "done"
        )
    )

    # Create the PrimerValidator system call using the supplied arguments.
    primer_command = (
        "primer_validator.py "
        f"-i {shlex.quote(inclusivity_directory)} "
        f"-e {shlex.quote(exclusivity_directory)} "
        f"-pf {shlex.quote(posixpath.join(run_directory, file_name))} "
        f"-r {shlex.quote(reports_directory)} "
        f"-m {shlex.quote(str(mismatches))} "
        f"-min {shlex.quote(str(min_amplicon_size))} "
        f"-max {shlex.quote(str(max_amplicon_size))} "
        f"-rb {shlex.quote(str(range_buffer))}"
    )

    # If contig breaks are to be permitted, add the flag to the command.
    if getattr(request, "contig_breaks", False):
        primer_command += " -cb"

    # Add the probe sequence. getattr() prevents AttributeError for request
    # types that do not define probe_sequence.
    if _validator_probe_records(request):
        primer_command += " -p {probe}".format(
            probe=shlex.quote(posixpath.join(run_directory, "probe.fasta"))
        )

    command_parts.append(primer_command)
    command_parts.append(
        f'cp -R {shlex.quote(reports_directory)} "$mount_dir/"'
    )

    # Copy sequence-ID files back when at least one such file exists. The
    # guarded loop prevents cp from receiving an unmatched filename pattern
    # when no panel ZIP archives were selected.
    command_parts.append(
        (
            f"for seqids_file in {shlex.quote(run_directory)}/*_seqids.txt; do "
            '[ -e "$seqids_file" ] || continue; '
            'cp "$seqids_file" "$mount_dir/"; '
            "done"
        )
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
    blob_client = get_storage_client()

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
        if not blob_client.object_exists(container_name, blob_name):
            raise FileNotFoundError(
                "Required Batch input blob does not exist: "
                f"{container_name}/{blob_name}"
            )

        sas_url = blob_client.create_transfer_url(
            container_name,
            "download",
            name=blob_name,
            expires_in_seconds=expiry_hours * 60 * 60,
        )

        input_blobs.append({"blob_name": blob_name, "sas_url": sas_url})

    return input_blobs


def _handle_panel_files(
    *,  # Force the use of keyword arguments
    container_name: str,
    inclusivity: bool,
    request: Any,
) -> list:
    """
    Copy selected benchmark archives and individual FASTA files into a panel.
    An empty panel gets a placeholder object so the Batch task can mount it.

    Args:
        container_name (str): Name of the container.
        inclusivity (bool): True if handling inclusivity panel, False for
            exclusivity.
        request (Any): The PrimerVerifier or PrimerValidator request.

    Returns:
        list: Blob names of the benchmark ZIP files required by the Batch
            task. The list is empty when no panel was selected.
    """
    # Determine the panel type
    panel_type = "inclusivity" if inclusivity else "exclusivity"

    benchmark_blob_names = []
    selected_benchmarks = []
    for selected_benchmark in (
        getattr(request, panel_type + '_panel', []) or []
    ):
        benchmark_name = validate_benchmark_panel(
            benchmark_name=selected_benchmark,
            panel=panel_type,
        )
        if benchmark_name not in selected_benchmarks:
            selected_benchmarks.append(benchmark_name)

    scalar_benchmark = getattr(request, panel_type + '_benchmark', '')
    if scalar_benchmark:
        benchmark_name = validate_benchmark_panel(
            benchmark_name=scalar_benchmark,
            panel=panel_type,
        )
        if benchmark_name not in selected_benchmarks:
            selected_benchmarks.append(benchmark_name)

    for benchmark_name in selected_benchmarks:
        benchmark_blob_name = copy_benchmark_files(
            container_name=container_name,
            genus=benchmark_name,
            panel=panel_type,
        )
        benchmark_blob_names.append(benchmark_blob_name)
        archive_info = benchmark_archive_info(
            blob_name=posixpath.basename(benchmark_blob_name),
            benchmark_name=benchmark_name,
        )
        if archive_info and archive_info['version']:
            versions = dict(getattr(request, 'benchmark_versions', {}) or {})
            versions[archive_info['benchmark_name']] = archive_info['version']
            request.benchmark_versions = versions
            request.save(update_fields=['benchmark_versions'])

    from olc_webportalv2.metadata.models import SequenceData

    query_genus = getattr(request, panel_type + '_genus', '')
    selected_seqids = set(
        getattr(request, panel_type + '_seqids', []) or []
    )
    if query_genus:
        selected_seqids.update(
            SequenceData.objects.filter(
                genus__iexact=query_genus
            ).values_list('seqid', flat=True)
        )
        if not selected_seqids:
            raise ValueError(
                f'No sequences were found for selected genus: {query_genus}'
            )
    if selected_seqids:
        benchmark_blob_names.extend(
            _copy_sequence_files(
                container_name=container_name,
                panel=panel_type,
                seqids=sorted(selected_seqids),
            )
        )

    if not benchmark_blob_names:
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


def copy_benchmark_files(
    *,  # Force the use of keyword arguments
    container_name: str,
    genus: str,
    panel: str
) -> str:
    """
    Copy the benchmark files to the appropriate folder.

    Select the highest available numeric archive version, falling back to the
    unversioned archive. Reuse an existing destination when its size matches;
    replace it when the source differs.

    Args:
        container_name (str): The name of the container to which the benchmark
            files are to be copied.
        genus (str): The canonical benchmark dataset identifier.
        panel (str): The panel (inclusivity/exclusivity) for which the
            benchmark files are to be copied.

    Returns:
        str: The blob name of the benchmark archive in the request container.
    """
    blob_client = get_storage_client()

    source_container_name = "benchmark-datasets"
    source_objects = blob_client.list_objects(
        source_container_name,
        prefix=genus,
    )
    selected_archive = select_benchmark_blob(
        blob_names=[item.name for item in source_objects],
        benchmark_name=genus,
    )
    if selected_archive is None:
        raise FileNotFoundError(
            f"No benchmark archive is available for dataset: {genus}"
        )
    source_blob_name = selected_archive['blob_name']
    destination_blob_name = posixpath.join(panel, source_blob_name)

    # Retrieve the authoritative size of the benchmark source blob.
    source_size = blob_client.get_object(
        source_container_name, source_blob_name
    ).size
    destination_exists = blob_client.object_exists(
        container_name, destination_blob_name
    )

    if destination_exists:
        destination_size = blob_client.get_object(
            container_name, destination_blob_name
        ).size
        if destination_size == source_size:
            return destination_blob_name

        blob_client.delete_object(container_name, destination_blob_name)

    blob_client.copy_object(
        source_container_name,
        source_blob_name,
        container_name,
        destination_blob_name,
    )

    return destination_blob_name


def _copy_sequence_files(*, container_name: str, panel: str, seqids: list) -> list:
    """Copy selected processed-data FASTA objects into a request panel."""
    blob_client = get_storage_client()
    source_container = 'processed-data'
    copied_names = []

    for seqid in sorted(set(seqids)):
        source_name = f'{seqid}.fasta'
        destination_name = posixpath.join(panel, source_name)
        if not blob_client.object_exists(source_container, source_name):
            raise FileNotFoundError(
                f'Selected SeqID is missing from processed-data: {seqid}'
            )

        source_size = blob_client.get_object(
            source_container, source_name
        ).size
        if blob_client.object_exists(container_name, destination_name):
            destination_size = blob_client.get_object(
                container_name, destination_name
            ).size
            if destination_size == source_size:
                copied_names.append(destination_name)
                continue
            blob_client.delete_object(container_name, destination_name)

        blob_client.copy_object(
            source_container,
            source_name,
            container_name,
            destination_name,
        )
        copied_names.append(destination_name)

    return copied_names


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
    blob_client = get_storage_client()
    blob_client.upload_bytes(
        container_name,
        os.path.join(panel, 'empty.txt'),
        b'',
    )


def check_verifier_tasks():
    """
    Check the status of tasks. If task fails, perform clean-up. If task
    succeeds, perform necessary steps and clean-up.
    """
    # Retrieve all VerifierAzureRequest objects
    verifier_tasks = VerifierAzureRequest.objects.filter()

    # Create a batch client
    batch_client = create_batch_client()

    for task in verifier_tasks:
        _process_verifier_task(
            batch_client=batch_client,
            task=task
        )


def _process_verifier_task(
    *,  # Force the use of keyword arguments
    batch_client,
    task: VerifierAzureRequest
):
    """
    Process an individual verifier task.

    Args:
        batch_client: The Azure Batch client.
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
    batch_client,
    batch_job_name: str
) -> bool:
    """
    Check if all tasks in the batch job have completed.

    Args:
        batch_client: The Azure Batch client.
        batch_job_name (str): The name of the batch job.

    Returns:
        bool: True if all tasks have completed, False otherwise.
    """
    try:
        for cloud_task in batch_client.task.list(batch_job_name):
            if cloud_task.state != "completed":
                return False
        return True
    except Exception:
        return False


def _check_exit_codes(
    *,  # Force the use of keyword arguments
    batch_client,
    batch_job_name: str
) -> bool:
    """
    Check if all tasks in the batch job have an exit code of 0.

    Args:
        batch_client: The Azure Batch client.
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
        subject = 'PrimerVerifier Analysis ' \
        f'"{verifier_request.project_name}" Complete'
        body = (
            f'Dear {verifier_request.user},\n'
            f'Your PrimerVerifier analysis, '
            f'"{verifier_request.project_name}", is complete.\n'
            f'Raw PrimerVerifier outputs are available here: '
            f'{report_sas_url}.\n\n'
            f'Summarised outputs are available here: {summary_sas_url}.\n\n'
            'Best regards,\n'
            'The FoodPort development team'
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
        subject = f'PrimerVerifier Analysis ' \
                  f'"{verifier_request.project_name}" Failed'
        body = (
            f'Dear {verifier_request.user},\n'
            f'Your PrimerVerifier analysis, '
            f'"{verifier_request.project_name}", has failed.\n'
            'Sorry for the inconvenience,\n'
            'The FoodPort development team'
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
        blob_client = get_storage_client()

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
    blob_client,
    container_name: str
):
    """
    Delete .fasta and .zip files from the blob container.

    Args:
        blob_client: Provider-neutral Storage client.
        container_name (str): The name of the container.
    """
    # Iterate through the blobs in the container
    for blob in blob_client.list_objects(container_name):
        if blob.name.endswith(('.fasta', '.zip')):
            blob_client.delete_object(container_name, blob.name)


def _generate_sas_urls(
    *,  # Force the use of keyword arguments
    blob_client,
    container_name: str
) -> Tuple[str, str]:
    """
    Generate SAS URLs for the report and summary files.

    Args:
        blob_client: Provider-neutral Storage client.
        container_name (str): The name of the container.

    Returns:
        Tuple[str, str]: The report and summary SAS URLs.
    """
    excel_blob_name = f"reports/{container_name}_summary_report.xlsx"
    archive_blob_name = f"{container_name}_reports.zip"

    excel_sas_url = blob_client.create_transfer_url(
        container_name, "download", name=excel_blob_name,
        expires_in_seconds=8 * 24 * 60 * 60,
    )
    archive_sas_url = blob_client.create_transfer_url(
        container_name, "download", name=archive_blob_name,
        expires_in_seconds=8 * 24 * 60 * 60,
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
        _prepare_validator_analysis(validator_request_pk=validator_request_pk)
    except Exception as exc:
        _handle_primer_error(
            exception=exc,
            request_model=ValidatorRequest,
            request_pk=validator_request_pk,
        )


def _prepare_validator_analysis(*, validator_request_pk):
    """Upload shared inputs and submit separate primer and gene analyses."""
    request = ValidatorRequest.objects.get(pk=validator_request_pk)
    container_name, primer_file_name = upload_primers(request=request)
    input_blob_names = [primer_file_name]

    probes = _validator_probe_records(request)
    if probes:
        upload_probe(request=request)
        input_blob_names.append('probe.fasta')
        request.probe_sequence = 'configured'

    for inclusivity in (True, False):
        input_blob_names.extend(_handle_panel_files(
            container_name=container_name,
            inclusivity=inclusivity,
            request=request,
        ))

    has_gene_targets = _upload_validator_gene_targets(
        container_name=container_name,
        request=request,
    )
    if has_gene_targets:
        input_blob_names.append('targets/query.fasta')
    input_blobs = _generate_input_blob_sas_urls(
        container_name=container_name,
        blob_names=input_blob_names,
    )

    primer_task, _ = ValidatorAnalysisTask.objects.get_or_create(
        validator_request=request,
        task_type='primer',
    )
    primer_task.input_manifest = input_blobs
    primer_task.save(update_fields=['input_manifest'])
    _submit_validator_task(
        request=request,
        analysis_task=primer_task,
        command=_create_validator_primer_task_command(
            request=request,
            container_name=container_name,
            file_name=primer_file_name,
            input_blobs=input_blobs,
        ),
    )

    if has_gene_targets:
        gene_task, _ = ValidatorAnalysisTask.objects.get_or_create(
            validator_request=request,
            task_type='gene',
        )
        gene_task.input_manifest = input_blobs
        gene_task.save(update_fields=['input_manifest'])
        _submit_validator_task(
            request=request,
            analysis_task=gene_task,
            command=_create_validator_gene_task_command(
                request=request,
                container_name=container_name,
                input_blobs=input_blobs,
            ),
        )


def _upload_validator_gene_targets(*, container_name, request):
    """Normalize pasted/uploaded target references for GeneSeekr."""
    records = []
    seen = set()

    def add_record(header, sequence):
        sequence = ''.join(str(sequence or '').split()).upper()
        header = str(header or '').strip().lstrip('>')
        if not header or not sequence:
            raise ValueError('Gene reference headers and sequences are required.')
        invalid_bases = sorted(set(sequence).difference('ACGTN'))
        if invalid_bases:
            raise ValueError(
                'Gene reference {0} contains invalid bases: {1}'.format(
                    header, ', '.join(invalid_bases)
                )
            )
        identifier = header.split(None, 1)[0]
        unique_id = identifier
        suffix = 2
        while unique_id in seen:
            unique_id = f'{identifier}__{suffix}'
            suffix += 1
        seen.add(unique_id)
        records.append((unique_id, sequence))

    targets = ValidatorTarget.objects.filter(
        sample__validator_request=request
    ).select_related('sample')
    for target in targets:
        prefix = f'{target.sample.name}__{target.name}'
        pasted = (target.gene_sequence or '').strip()
        if pasted:
            if pasted.startswith('>'):
                parsed = list(SeqIO.parse(StringIO(pasted), 'fasta'))
                if not parsed:
                    raise ValueError(
                        f'Unable to parse FASTA for target {target.name}.'
                    )
                for record in parsed:
                    add_record(
                        f'{prefix}__{record.id}', str(record.seq)
                    )
            else:
                add_record(prefix, pasted)

        reference_fasta = target.reference_fasta
        if reference_fasta:
            try:
                reference_fasta.open('rb')
                contents = reference_fasta.read()
                if isinstance(contents, bytes):
                    contents = contents.decode('utf-8')
            except (OSError, UnicodeDecodeError) as exc:
                raise ValueError(
                    f'Unable to read FASTA for target {target.name}: {exc}'
                ) from exc
            finally:
                reference_fasta.close()
            parsed = list(SeqIO.parse(StringIO(contents), 'fasta'))
            if not parsed:
                raise ValueError(
                    f'Uploaded FASTA for target {target.name} contains no records.'
                )
            for record in parsed:
                add_record(
                    f'{prefix}__{record.id}', str(record.seq)
                )

    if not records:
        return False
    contents = ''.join(f'>{name}\n{seq}\n' for name, seq in records)
    storage = get_storage_client()
    storage.ensure_container(container_name)
    storage.upload_bytes(
        container_name, 'targets/query.fasta', contents.encode('utf-8')
    )
    return True


def _submit_validator_task(*, request, analysis_task, command):
    job_name = f'{request.container_namer()}-{analysis_task.task_type}'
    try:
        submission = generic_api_submit(
            command=command,
            container_name=request.container_namer(),
            vm_size='Standard_D4ds_v5',
            unique_id=analysis_task.task_type,
            log_prefix=analysis_task.task_type,
        )
        if (
            not isinstance(submission, dict)
            or submission.get('status') != 'Success'
        ):
            raise RuntimeError(
                f'Validator Batch submission failed: {submission}'
            )
    except Exception as exc:
        analysis_task.status = 'error'
        analysis_task.error = str(exc)
        analysis_task.save(update_fields=['status', 'error'])
        raise
    task_ids = submission.get('tasks') or []
    analysis_task.batch_job_id = submission.get('job_id') or job_name
    analysis_task.batch_task_id = task_ids[0] if task_ids else ''
    analysis_task.status = 'submitted'
    analysis_task.error = ''
    analysis_task.save(update_fields=[
        'batch_job_id', 'batch_task_id', 'status', 'error'
    ])


def _create_validator_primer_task_command(
    *, request, container_name, file_name, input_blobs
):
    return _create_primer_cmd(
        container_name=f'{container_name}-primer',
        file_name=file_name,
        input_blobs=input_blobs,
        path=f'$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}',
        request=request,
    )


def _create_validator_gene_task_command(
    *, request, container_name, input_blobs
):
    job_name = f'{container_name}-gene'
    run_directory = f'/datadrive/{job_name}'
    mount_dir = f'$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}'
    sequences = posixpath.join(run_directory, 'sequences')
    targets = posixpath.join(run_directory, 'targets')
    reports = posixpath.join(run_directory, 'reports')
    parts = [
        'source $CONDA/activate /envs/cowbat',
        'set -euo pipefail',
        f'mkdir -p {shlex.quote(sequences)} {shlex.quote(targets)} {shlex.quote(reports)}',
    ]
    for item in input_blobs:
        destination = posixpath.join(run_directory, item['blob_name'])
        parts.extend((
            f'mkdir -p {shlex.quote(posixpath.dirname(destination))}',
            'curl --fail --location --retry 5 --output {0} {1}'.format(
                shlex.quote(destination), shlex.quote(item['sas_url'])
            ),
        ))
    parts.extend((
        'for archive in {0}/inclusivity/*.zip {0}/exclusivity/*.zip; do '
        '[ -e "$archive" ] || continue; unzip -n -q "$archive" -d {1}; done'.format(
            shlex.quote(run_directory), shlex.quote(sequences)
        ),
        f'GeneSeekr blastn -u -s {shlex.quote(sequences)} -t {shlex.quote(targets)} -r {shlex.quote(reports)}',
        f'mkdir -p {mount_dir}/reports/gene',
        f'cp -R {shlex.quote(reports)}/. {mount_dir}/reports/gene/',
    ))
    return ' && '.join(parts)


def _normalize_gene_reports(gene_reports):
    """Normalize GeneSeekr JSON, TSV, and CSV output records."""
    normalized = []
    for report_path, contents in gene_reports.items():
        lower_path = report_path.lower()
        if lower_path.endswith('.json'):
            try:
                payload = json.loads(contents)
            except (TypeError, ValueError):
                logger.warning('Skipping invalid GeneSeekr JSON report %s', report_path)
                continue
            records = payload if isinstance(payload, list) else [payload]
            for record in records:
                if isinstance(record, dict):
                    normalized.append(_normalize_gene_record(report_path, record))
        elif lower_path.endswith(('.tsv', '.csv')):
            delimiter = '\t' if lower_path.endswith('.tsv') else ','
            try:
                records = csv.DictReader(
                    StringIO(contents), delimiter=delimiter
                )
                normalized.extend(
                    _normalize_gene_record(report_path, record)
                    for record in records
                )
            except (csv.Error, TypeError):
                logger.warning(
                    'Skipping malformed GeneSeekr tabular report %s',
                    report_path,
                    exc_info=True,
                )
    return [record for record in normalized if record]


def _normalize_gene_record(report_path, record):
    """Map GeneSeekr field-name variants to the report-analysis contract."""
    def value(*names):
        for name in names:
            if record.get(name) not in ('', None):
                return record[name]
        return None

    return {
        'report': report_path,
        'query': value(
            'query', 'Query', 'query_id', 'sequence', 'Sequence', 'seqid'
        ),
        'target': value(
            'target', 'Target', 'subject_id', 'gene', 'Gene', 'reference'
        ),
        'hit': value(
            'hit', 'Hit', 'subject', 'Subject', 'subject_id', 'match'
        ),
        'identity': value(
            'identity', 'Identity', '% identity', 'percent_identity',
            'percent_match',
        ),
        'coverage': value(
            'coverage', 'Coverage', '% coverage', 'percent_coverage'
        ),
        'evalue': value('evalue', 'E-value', 'Evalue'),
        'query_start': value('query_start', 'qstart', 'contig_start'),
        'query_end': value('query_end', 'qend', 'contig_end'),
        'query_length': value('query_length', 'qlen', 'contig_length'),
        'query_frame': value('query_frame', 'qframe', 'frame'),
        'query_sequence': value(
            'query_sequence', 'qseq', 'query_alignment'
        ),
        'subject_start': value('subject_start', 'sstart', 'target_start'),
        'subject_end': value('subject_end', 'send', 'target_end'),
        'subject_length': value(
            'subject_length', 'slen', 'target_length'
        ),
        'alignment_length': value(
            'alignment_length', 'length', 'alignment_len'
        ),
        'raw': record,
    }


def check_validator_tasks():
    """
    Check the status of ValidatorAzureRequest tasks. If a task fails, perform
    clean-up. If a task succeeds, perform necessary steps and clean-up.
    """
    validator_tasks = PrimerValidatorAzureRequest.objects.filter()
    batch_client = create_batch_client()

    for analysis_task in ValidatorAnalysisTask.objects.filter(
        status='submitted'
    ).select_related('validator_request'):
        _process_validator_analysis_task(
            batch_client=batch_client,
            task=analysis_task,
        )

    for analysis_task in ValidatorAnalysisTask.objects.filter(
        status__in=('complete', 'error')
    ).exclude(batch_job_id='').select_related('validator_request'):
        if _delete_validator_batch_resources(
            batch_client=batch_client,
            job_id=analysis_task.batch_job_id,
        ):
            analysis_task.batch_job_id = ''
            analysis_task.batch_task_id = ''
            analysis_task.save(update_fields=['batch_job_id', 'batch_task_id'])

    for task in validator_tasks:
        _process_validator_task(
            batch_client=batch_client,
            task=task
        )


def _process_validator_analysis_task(*, batch_client, task):
    """Poll one of a request's independent Batch analyses."""
    try:
        cloud_tasks = list(batch_client.task.list(task.batch_job_id))
    except Exception:
        logger.exception(
            'Unable to poll Validator Batch task %s for request %s',
            task.task_type,
            task.validator_request_id,
        )
        return
    if not cloud_tasks or any(item.state != 'completed' for item in cloud_tasks):
        return

    exit_codes = [
        getattr(item.execution_info, 'exit_code', None)
        for item in cloud_tasks
    ]
    successful = all(code == 0 for code in exit_codes)
    task.status = 'complete' if successful else 'error'
    task.error = '' if successful else (
        f'Batch task returned non-zero or missing exit codes: {exit_codes}'
    )
    task.save(update_fields=['status', 'error'])

    request = task.validator_request
    if not successful:
        request.status = 'Error'
        request.errors = task.error
        request.save(update_fields=['status', 'errors'])

    try:
        analysis_tasks = list(request.analysis_tasks.all())
        if all(item.status not in ('pending', 'submitted') for item in analysis_tasks):
            if any(item.status == 'error' for item in analysis_tasks):
                _handle_failed_validator_run(validator_request=request)
            else:
                _handle_successful_validator_run(
                    batch_job_name=request.container_namer(),
                    validator_request=request,
                )
    except Exception as exc:
        request.status = 'Error'
        request.errors = f'Validator report finalization failed: {exc}'
        request.save(update_fields=['status', 'errors'])
        logger.exception(
            'Unable to finalize Validator request %s', request.pk
        )
    finally:
        if _delete_validator_batch_resources(
            batch_client=batch_client,
            job_id=task.batch_job_id,
        ):
            task.batch_job_id = ''
            task.batch_task_id = ''
            task.save(update_fields=['batch_job_id', 'batch_task_id'])


def _delete_validator_batch_resources(*, batch_client, job_id):
    """Delete the completed Batch job and its conventional pool."""
    try:
        batch_client.job.delete(job_id=job_id)
    except BatchServiceError as exc:
        if exc.status_code != 404:
            logger.warning(
                'Batch cleanup failed for Validator job %s: %s', job_id, exc
            )
            return False
    except Exception:
        logger.exception(
            'Batch cleanup failed for Validator job %s', job_id
        )
        return False
    pool_id = batch_pool_id_from_job_id(job_id)
    try:
        if batch_client.pool.exists(pool_id):
            batch_client.pool.delete(pool_id=pool_id)
    except BatchServiceError as exc:
        if exc.status_code != 404:
            logger.warning(
                'Batch pool cleanup failed for Validator job %s: %s',
                job_id,
                exc,
            )
            return False
    except Exception:
        logger.exception(
            'Batch pool cleanup failed for Validator job %s', job_id
        )
        return False
    return True


def _process_validator_task(
    *,  # Force the use of keyword arguments
    batch_client,
    task: PrimerValidatorAzureRequest
):
    """
    Process an individual validator task.

    Args:
        batch_client: The Azure Batch client.
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
        subject = f'PrimerValidator Analysis ' \
            f'"{validator_request.project_name}" Complete'
        body = (
            f"Dear {validator_request.user},\n"
            f"Your PrimerValidator analysis, "
            f'"{validator_request.project_name}", is complete.\n'
            f"Raw PrimerValidator outputs are available "
            f'here: {report_sas_url}.\n\n'
            f"Summarised outputs are available here: {summary_sas_url}.\n\n"
            "Best regards,\n"
            "The FoodPort development team"
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
        subject = f'PrimerValidator Analysis ' \
                  f'"{validator_request.project_name}" Failed'
        body = (
            f"Dear {validator_request.user},\n"
            f'Your PrimerValidator analysis, '
            f'"{validator_request.project_name}", has failed.\n'
            "Sorry for the inconvenience,\n"
            "The FoodPort development team"
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
        blob_client = get_storage_client()

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

        gene_reports = {}
        gene_report_folder = os.path.join(report_folder, 'gene')
        for root, _, filenames in os.walk(gene_report_folder):
            for filename in filenames:
                report_path = os.path.join(root, filename)
                relative_path = os.path.relpath(
                    report_path, gene_report_folder
                )
                try:
                    with open(report_path, 'r', encoding='utf-8') as source:
                        gene_reports[relative_path] = source.read()
                except (OSError, UnicodeDecodeError):
                    logger.warning(
                        'Unable to read GeneSeekr output %s',
                        report_path,
                        exc_info=True,
                    )

        validator_request.gene_report = json.dumps(
            gene_reports, sort_keys=True
        )
        validator_request.gene_results = _normalize_gene_reports(gene_reports)
        validator_request.validator_original_report = validator_request.report
        salmonella_classification = validator_request.samples.filter(
            classification_rule='salmonella'
        ).exists()
        allowed_targets = ('inva', 'stn') if salmonella_classification else None
        categories = categorize_gene_hits(
            validator_request.gene_results,
            allowed_targets=allowed_targets,
        )
        category_details = categorize_gene_hit_details(
            validator_request.gene_results,
            allowed_targets=allowed_targets,
        )
        selected_categories = set(
            validator_request.gene_exclusion_categories or []
        )
        validator_request.gene_exclusion_results = categories
        validator_request.gene_exclusion_details = category_details
        validator_request.gene_exclusion_note = gene_exclusion_note(
            selected_categories, categories
        )
        validator_request.report = filter_report_by_gene_categories(
            validator_request.report, categories, selected_categories
        )
        validator_request.save(update_fields=[
            'gene_report',
            'gene_results',
            'validator_original_report',
            'gene_exclusion_results',
            'gene_exclusion_details',
            'gene_exclusion_note',
            'report',
        ])

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
