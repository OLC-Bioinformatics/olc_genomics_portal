#!/usr/bin/env python

"""
This module contains tasks for processing primer validation and verification
requests.
"""

# Standard imports
import datetime
import csv
import json
import logging
import os
import posixpath
import shlex
import shutil
import time
import traceback
from io import StringIO
from typing import (
    Any,
    Tuple
)

# Django imports
from django.conf import settings
from django.db.models import Q

# Third-party imports
import azure.batch.batch_service_client as batch
from azure.storage.blob import BlockBlobService
from azure.storage.blob import BlobPermissions
from azure.batch import batch_auth
import azure.batch.models as batchmodels
from celery import shared_task
from Bio import SeqIO

# Portal-specific imports
from olc_webportalv2.common.batch_recovery import (
    _batch_task_exit_codes,
    batch_pool_id_from_job_id,
    retry_failed_pool_resize
)
from olc_webportalv2.common.methods import generic_api_submit
from olc_webportalv2.primer_finder.methods import (
    _archive_reports_and_upload,
    create_details,
    populate_report,
    send_email,
    upload_primers,
    upload_probe,
    _validator_probe_records,
    _upload_summary_excel,
    categorize_gene_hit_details,
    categorize_gene_hits,
    filter_report_by_gene_categories,
    gene_exclusion_note
)

from olc_webportalv2.primer_finder.models import (
    ValidatorAzureRequest,
    VerifierRequest,
    ValidatorAnalysisTask,
    ValidatorPanel,
    ValidatorPrimerSet,
    ValidatorRequest,
    ValidatorTarget,
    VerifierAnalysisTask,
    VerifierAzureRequest,
    VerifierPanel,
    VerifierPrimerSet
)
from olc_webportalv2.common.benchmarks import (
    BENCHMARK_CONTAINER_NAME,
    benchmark_archive_info,
    select_benchmark_blob,
    validate_benchmark_panel,
)


logger = logging.getLogger(__name__)


def _validator_vm_sizes(input_blob_names):
    """Return primer and gene VM sizes for staged Validator inputs.

    Large NCBI benchmark archives contain thousands of genomes and need more
    CPU, memory, and temporary disk than ordinary Validator workloads. Detect
    them from the authoritative staged blob names rather than project names or
    user-interface labels.

    Args:
        input_blob_names (list): Blob names prepared for the request.

    Returns:
        tuple: Primer VM size, gene VM size, and large-benchmark boolean.
    """
    large_benchmark = False

    for blob_name in input_blob_names:
        filename = posixpath.basename(str(blob_name)).lower()
        if filename.startswith('ncbi-') and filename.endswith('.zip'):
            large_benchmark = True
            break

    if large_benchmark:
        primer_vm_size = getattr(
            settings,
            'PRIMER_VALIDATOR_LARGE_PRIMER_VM_SIZE',
            'Standard_D16ds_v5',
        )
        gene_vm_size = getattr(
            settings,
            'PRIMER_VALIDATOR_LARGE_GENE_VM_SIZE',
            'Standard_D16ds_v5',
        )
    else:
        primer_vm_size = getattr(
            settings,
            'PRIMER_VALIDATOR_DEFAULT_PRIMER_VM_SIZE',
            'Standard_D4ds_v5',
        )
        gene_vm_size = getattr(
            settings,
            'PRIMER_VALIDATOR_DEFAULT_GENE_VM_SIZE',
            'Standard_D4ds_v5',
        )

    return primer_vm_size, gene_vm_size, large_benchmark


def _verifier_vm_sizes(input_blob_names):
    """Return primer and gene VM sizes for staged Verifier inputs."""
    large_benchmark = False

    for blob_name in input_blob_names:
        filename = posixpath.basename(str(blob_name)).lower()
        if filename.startswith('ncbi-') and filename.endswith('.zip'):
            large_benchmark = True
            break

    if large_benchmark:
        primer_vm_size = getattr(
            settings,
            'PRIMER_VERIFIER_LARGE_PRIMER_VM_SIZE',
            'Standard_D16ds_v5'
        )
        gene_vm_size = getattr(
            settings,
            'PRIMER_VERIFIER_LARGE_GENE_VM_SIZE',
            'Standard_D16ds_v5'
        )
    else:
        primer_vm_size = getattr(
            settings,
            'PRIMER_VERIFIER_DEFAULT_PRIMER_VM_SIZE',
            'Standard_D4ds_v5'
        )
        gene_vm_size = getattr(
            settings,
            'PRIMER_VERIFIER_DEFAULT_GENE_VM_SIZE',
            'Standard_D8s_v3'
        )

    return primer_vm_size, gene_vm_size, large_benchmark


def _format_task_error(exception, stage):
    """Return a persisted error with context and the full traceback."""
    traceback_text = traceback.format_exc()
    if traceback_text.strip() == 'NoneType: None':
        traceback_text = ''.join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )
    return (
        'Stage: {0}\n'
        'Exception: {1}: {2}\n'
        'Traceback:\n{3}'
    ).format(
        stage,
        type(exception).__name__,
        str(exception),
        traceback_text,
    )


def _record_request_error(*, request, exception, stage, task=None):
    """Persist detailed task and request errors without masking the failure."""
    error_text = _format_task_error(exception, stage)
    if task is not None:
        task.status = 'error'
        task.error = error_text
        task.save(update_fields=['status', 'error'])
    request.status = 'Error'
    request.errors = error_text
    request.save(update_fields=['status', 'errors'])
    logger.error(error_text)
    return error_text


@shared_task
def run_primer_verifier(
    *,  # Force the use of keyword arguments
    verifier_request_pk: int
):
    """
    Run the PrimerVerifier analysis.

    Args:
        verifier_request_pk (int): The primary key of the
            VerifierRequest object for which the analysis is to be run.
    """
    try:
        _prepare_verifier_analysis(verifier_request_pk=verifier_request_pk)
    except Exception as exc:
        _handle_primer_error(
            exception=exc,
            request_model=VerifierRequest,
            request_pk=verifier_request_pk
        )


def _prepare_verifier_analysis(*, verifier_request_pk):
    """Stage shared inputs and submit separate primer and GeneSeekr jobs."""
    request = VerifierRequest.objects.get(pk=verifier_request_pk)

    _validate_request_panel_selections(request=request, panel_model=VerifierPanel)

    container_name, primer_file_name = upload_primers(request=request)
    input_blob_names = [primer_file_name]

    if request.probe_sequence:
        upload_probe(request=request)
        input_blob_names.append('probe.fasta')

    input_blob_names.extend(_handle_panel_files(
        container_name=container_name,
        inclusivity=True,
        panel_model=VerifierPanel,
        request_pk=request.pk
    ))
    input_blob_names.extend(_handle_panel_files(
        container_name=container_name,
        inclusivity=False,
        panel_model=VerifierPanel,
        request_pk=request.pk
    ))

    input_blobs = _generate_input_blob_sas_urls(
        container_name=container_name,
        blob_names=input_blob_names
    )
    primer_vm_size, gene_vm_size, large_benchmark = _verifier_vm_sizes(
        input_blob_names
    )
    logger.info(
        'Verifier VM selection: request=%s large_ncbi_benchmark=%s '
        'primer_vm_size=%s gene_vm_size=%s',
        request.pk,
        large_benchmark,
        primer_vm_size,
        gene_vm_size
    )

    primer_task, gene_task = _verifier_tasks(request)
    for analysis_task in (primer_task, gene_task):
        analysis_task.input_manifest = input_blobs
        analysis_task.save(update_fields=['input_manifest'])

    _submit_verifier_task(
        request=request,
        analysis_task=primer_task,
        command=_create_focused_primer_command(
            request=request,
            container_name=container_name,
            file_name=primer_file_name,
            input_blobs=input_blobs
        ),
        vm_size=primer_vm_size
    )
    _submit_verifier_task(
        request=request,
        analysis_task=gene_task,
        command=_create_focused_geneseekr_command(
            container_name=container_name,
            primer_file_name=primer_file_name,
            input_blobs=input_blobs
        ),
        vm_size=gene_vm_size
    )

def _verifier_tasks(request):
    """Return the independent PrimerVerifier and GeneSeekr task rows."""
    primer_task, unused_created = VerifierAnalysisTask.objects.get_or_create(
        verifier_request=request,
        task_type='primer',
    )
    gene_task, unused_created = VerifierAnalysisTask.objects.get_or_create(
        verifier_request=request,
        task_type='gene',
    )
    return primer_task, gene_task


def _submit_verifier_task(*, request, analysis_task, command, vm_size):
    """Submit one focused Verifier job and persist its Batch identifiers."""
    job_name = '{0}-{1}'.format(
        request.container_namer(), analysis_task.task_type
    )
    submission = generic_api_submit(
        command=command,
        container_name=request.container_namer(),
        input_file_pattern=None,
        vm_size=vm_size,
        unique_id=analysis_task.task_type,
        log_prefix=analysis_task.task_type,
    )
    if not isinstance(submission, dict) or submission.get('status') != 'Success':
        raise RuntimeError('Verifier Batch submission failed: {0}'.format(
            submission
        ))
    analysis_task.batch_job_id = submission.get('job_id') or job_name
    task_ids = submission.get('tasks') or []
    analysis_task.batch_task_id = task_ids[0] if task_ids else ''
    analysis_task.status = 'submitted'
    analysis_task.error = ''
    analysis_task.save(update_fields=[
        'batch_job_id', 'batch_task_id', 'status', 'error'
    ])


def _create_focused_primer_command(
    *, request, container_name, file_name, input_blobs
):
    """Build a local-download PrimerVerifier command."""
    path = '$AZ_BATCH_NODE_MOUNTS_DIR/{0}'.format(container_name)
    return _create_primer_cmd(
        container_name='{0}-primer'.format(container_name),
        file_name=file_name,
        input_blobs=input_blobs,
        path=path,
        request=request,
        clean_output=False
    )

def _create_focused_geneseekr_command(
    *, container_name, primer_file_name, input_blobs
):
    """Build a local-download GeneSeekr command for Verifier inputs."""
    run_directory = '/datadrive/{0}-gene'.format(container_name)
    mount_dir = '$AZ_BATCH_NODE_MOUNTS_DIR/{0}'.format(container_name)
    sequences = posixpath.join(run_directory, 'sequences')
    targets = posixpath.join(run_directory, 'targets')
    reports = posixpath.join(run_directory, 'reports')
    parts = [
        'source $CONDA/activate /envs/cowbat',
        'set -euo pipefail',
        'rm -rf {0}'.format(shlex.quote(run_directory)),
        'mkdir -p {0} {1} {2}'.format(
            shlex.quote(sequences),
            shlex.quote(targets),
            shlex.quote(reports)
        )
    ]

    for item in input_blobs:
        destination = posixpath.join(
            run_directory, item['blob_name']
        )
        partial = '{0}.part'.format(destination)
        parts.extend([
            'mkdir -p {0}'.format(
                shlex.quote(posixpath.dirname(destination))
            ),
            (
                'curl --fail --location --retry 5 --retry-delay 10 '
                '--connect-timeout 60 --continue-at - --output {0} {1}'
            ).format(
                shlex.quote(partial),
                shlex.quote(item['sas_url'])
            ),
            'test -s {0}'.format(shlex.quote(partial)),
            'mv {0} {1}'.format(
                shlex.quote(partial),
                shlex.quote(destination)
            ),
            'test -s {0}'.format(shlex.quote(destination))
        ])

    parts.extend([
        'cp {0} {1}'.format(
            shlex.quote(posixpath.join(run_directory, primer_file_name)),
            shlex.quote(posixpath.join(targets, 'query.fasta'))
        ),
        (
            'for panel in inclusivity exclusivity; do '
            'for input in {0}/$panel/*; do '
            '[ -e "$input" ] || continue; '
            'case "$input" in '
            '*.zip) unzip -tq "$input" && '
            'unzip -n -q "$input" -d {1} ;; '
            '*.fasta|*.fa|*.fna|*.fas) cp "$input" {1}/ ;; '
            'esac; done; done'
        ).format(
            shlex.quote(run_directory),
            shlex.quote(sequences)
        ),
        'GeneSeekr blastn -u -s {0} -t {1} -r {2}'.format(
            shlex.quote(sequences),
            shlex.quote(targets),
            shlex.quote(reports)
        ),
        'mkdir -p {0}/reports/gene'.format(mount_dir),
        'cp -R {0}/. {1}/reports/gene/'.format(
            shlex.quote(reports),
            mount_dir
        )
    ])
    return ' && '.join(parts)

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

    # Validate the inclusivity and exclusivity panel selections for the request.
    _validate_request_panel_selections(
        request=primer_request,
        panel_model=panel_model
    )

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
    if request_model == VerifierRequest:
        VerifierAzureRequest.objects.create(
            verifier_request=primer_request, exit_code_file="NA"
        )
    else:
        ValidatorAzureRequest.objects.create(
            validator_request=primer_request, exit_code_file="NA"
        )


def _create_primer_cmd(
    *, container_name: str, file_name: str, input_blobs: list, path: str,
    request: Any, clean_output: bool = True
) -> str:
    """
    Create the system call.

    The call includes activating the conda environment, downloading input
    files directly from Azure Blob Storage to the node SSD with curl,
    creating files with the sequence IDs for each ZIP file, unzipping the ZIP
    files, running the primer_validator.py script, and copying the reports
       request=request,
       clean_output=False,

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
    if _validator_probe_records(request):
        primer_command += " -p {probe_path}".format(
            probe_path=shlex.quote(posixpath.join(run_directory, "probe.fasta"))
        )

    command_parts.append(primer_command)

    # Copy the reports folder back to the BlobFuse-mounted request container.
    # Input files are no longer read through this mount, but the existing
    # output workflow is preserved.
    if clean_output:
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

    request_model = (
        VerifierRequest if panel_model == VerifierPanel else ValidatorRequest
    )
    primer_request = request_model.objects.get(pk=request_pk)
    benchmark_blob_names = []

    def copy_and_record_version(benchmark_name):
        canonical_name = validate_benchmark_panel(
            benchmark_name=benchmark_name, panel=panel_type
        )

        benchmark_blob_name = copy_benchmark_files(
            container_name=container_name,
            genus=canonical_name,
            panel=panel_type,
        )
        archive_info = benchmark_archive_info(
            blob_name=posixpath.basename(benchmark_blob_name),
            benchmark_name=canonical_name,
        )
        if archive_info and archive_info["version"]:
            versions = dict(primer_request.benchmark_versions or {})
            versions[archive_info["benchmark_name"]] = archive_info["version"]
            primer_request.benchmark_versions = versions
            primer_request.save(update_fields=["benchmark_versions"])

        return benchmark_blob_name

    # Check if any panel rows exist for the given request and panel type
    has_panel_rows = panel_filter.exists()

    # Copy the benchmark files if the panel is specified
    if has_panel_rows:
        for panel in panel_filter:
            benchmark_blob_name = copy_and_record_version(panel.genus)
            if benchmark_blob_name not in benchmark_blob_names:
                benchmark_blob_names.append(benchmark_blob_name)
    query_genus = getattr(
        primer_request, panel_type + '_genus', ''
    )
    query_seqids = getattr(
        primer_request, panel_type + '_seqids', []
    ) or []
    query_benchmark = getattr(
        primer_request, panel_type + '_benchmark', ''
    )
    if query_benchmark:
        benchmark_blob_name = copy_and_record_version(query_benchmark.lower())
        if benchmark_blob_name not in benchmark_blob_names:
            benchmark_blob_names.append(benchmark_blob_name)
    selected_seqids = set(query_seqids)
    if query_genus:
        from olc_webportalv2.metadata.models import SequenceData
        selected_seqids.update(SequenceData.objects.filter(
            genus__iexact=query_genus
        ).values_list('seqid', flat=True))
    if selected_seqids:
        benchmark_blob_names.extend(_copy_sequence_files(
            container_name=container_name,
            panel=panel_type,
            seqids=sorted(selected_seqids),
        ))
    if not has_panel_rows and not selected_seqids and not query_benchmark:
        create_empty_folder(container_name=container_name, panel=panel_type)

    return benchmark_blob_names


def _validate_request_panel_selections(*, request, panel_model):
    """
    Validate request-wide inclusivity and exclusivity benchmark selections.

    Exclusivity-only benchmarks may only be assigned to exclusivity.
    Ordinary benchmarks may be used in either panel, but the same benchmark
    may not be assigned to both panels within one request.
    """
    if panel_model == VerifierPanel:
        fk_name = "verifier_request_id"
    else:
        fk_name = "validator_request_id"

    inclusivity_names = set()
    exclusivity_names = set()

    # Validate the benchmark arrays stored directly on the request.
    for benchmark_name in getattr(request, "inclusivity_panel", []) or []:
        inclusivity_names.add(
            validate_benchmark_panel(benchmark_name=benchmark_name, panel="inclusivity")
        )

    for benchmark_name in getattr(request, "exclusivity_panel", []) or []:
        exclusivity_names.add(
            validate_benchmark_panel(benchmark_name=benchmark_name, panel="exclusivity")
        )

    # Validate the related panel rows as a secondary source.
    panel_rows = panel_model.objects.filter(**{fk_name: request.pk}).values_list(
        "panel", "genus"
    )

    for panel_type, benchmark_name in panel_rows:
        canonical_name = validate_benchmark_panel(
            benchmark_name=benchmark_name, panel=panel_type
        )

        if panel_type == "inclusivity":
            inclusivity_names.add(canonical_name)
        elif panel_type == "exclusivity":
            exclusivity_names.add(canonical_name)

    # VerifierRequest also supports scalar benchmark selections.
    inclusivity_benchmark = getattr(request, "inclusivity_benchmark", "")
    if inclusivity_benchmark:
        inclusivity_names.add(
            validate_benchmark_panel(
                benchmark_name=inclusivity_benchmark, panel="inclusivity"
            )
        )

    exclusivity_benchmark = getattr(request, "exclusivity_benchmark", "")
    if exclusivity_benchmark:
        exclusivity_names.add(
            validate_benchmark_panel(
                benchmark_name=exclusivity_benchmark, panel="exclusivity"
            )
        )

    overlap = inclusivity_names.intersection(exclusivity_names)
    if overlap:
        raise ValueError(
            "The following benchmark dataset(s) cannot be assigned to both "
            "inclusivity and exclusivity: {0}.".format(", ".join(sorted(overlap)))
        )

def _copy_sequence_files(*, container_name: str, panel: str,
                          seqids: list) -> list:
    """Copy selected processed-data FASTA files into a request panel."""
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY,
    )
    copied_names = []
    for seqid in seqids:
        source_blob_name = 'processed-data/{0}.fasta'.format(seqid)
        destination_blob_name = posixpath.join(
            panel, '{0}.fasta'.format(seqid)
        )
        if not blob_client.exists(
                container_name='processed-data', blob_name=source_blob_name):
            raise RuntimeError(
                'Selected SeqID is missing from processed-data: {0}'.format(
                    seqid
                )
            )
        if not blob_client.exists(
                container_name=container_name,
                blob_name=destination_blob_name):
            source_sas_token = blob_client.generate_blob_shared_access_signature(
                container_name='processed-data',
                blob_name=source_blob_name,
                permission=BlobPermissions.READ,
                expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=12),
            )
            blob_client.copy_blob(
                container_name=container_name,
                blob_name=destination_blob_name,
                copy_source=blob_client.make_blob_url(
                    container_name='processed-data',
                    blob_name=source_blob_name,
                    sas_token=source_sas_token,
                ),
            )
            _wait_for_blob_copy(
                blob_client=blob_client,
                container_name=container_name,
                blob_name=destination_blob_name,
            )
        copied_names.append(destination_blob_name)
    return copied_names


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

    error_text = _record_request_error(
        request=request,
        exception=exception,
        stage='analysis submission',
    )
    if request_model is ValidatorRequest:
        ValidatorAnalysisTask.objects.filter(
            validator_request=request,
            status='pending',
        ).update(status='error', error=error_text)
        if request.analysis_tasks.exists():
            _finalize_validator_request(request=request)


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

    source_container_name = BENCHMARK_CONTAINER_NAME
    source_blobs = [
        blob.name for blob in blob_client.list_blobs(
            container_name=source_container_name
        )
    ]
    selected_blob = select_benchmark_blob(
        blob_names=source_blobs, benchmark_name=genus
    )
    if selected_blob is None:
        raise FileNotFoundError(
            'No benchmark archive found for {0}.'.format(genus)
        )
    source_blob_name = selected_blob['blob_name']
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
    # Prefer the independent primer and GeneSeekr task rows. The legacy rows
    # remain supported so requests created before this split can finish.
    verifier_analysis_tasks = VerifierAnalysisTask.objects.filter(
        status='submitted'
    )
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

    for task in verifier_analysis_tasks:
        _process_verifier_analysis_task(
            batch_client=batch_client,
            task=task
        )
    terminal_tasks = VerifierAnalysisTask.objects.filter(
        status__in=('complete', 'error')
    ).exclude(batch_job_id='')
    for task in terminal_tasks:
        cleanup_complete = _delete_batch_resources(
            batch_client=batch_client,
            resource_id=task.batch_job_id
        )
        if cleanup_complete:
            _clear_analysis_task_batch_ids(task)

    for task in verifier_tasks:
        _process_verifier_task(
            batch_client=batch_client,
            task=task
        )


def _process_verifier_analysis_task(*, batch_client, task):
    """Poll one focused Verifier job and finalize when both jobs finish."""
    request = task.verifier_request
    job_id = task.batch_job_id
    pool_id = batch_pool_id_from_job_id(job_id)

    recovery = retry_failed_pool_resize(
        batch_client=batch_client,
        pool_id=pool_id,
        target_dedicated_nodes=1,
        target_low_priority_nodes=0
    )
    recovery_action = recovery.get('action')

    if recovery_action == 'resize_retried':
        logger.warning(
            'Verifier task waiting after pool resize retry: '
            'request=%s task=%s pool=%s',
            request.pk,
            task.task_type,
            pool_id
        )
        return

    if recovery_action in ('retry_throttled', 'resize_in_progress'):
        return

    if recovery_action in (
        'non_retryable_resize_error',
        'inspection_failed',
        'resize_request_failed',
        'unsupported_allocation_state'
    ):
        logger.error(
            'Verifier Batch pool recovery requires attention: '
            'request=%s task=%s pool=%s result=%s',
            request.pk,
            task.task_type,
            pool_id,
            recovery
        )

    if not _check_tasks_completed(
        batch_client=batch_client,
        batch_job_name=job_id
    ):
        return

    try:
        exit_codes = _batch_task_exit_codes(
            batch_client=batch_client,
            batch_job_name=job_id
        )
        successful = bool(exit_codes) and all(
            item['exit_code'] == 0
            for item in exit_codes
        )

        task.status = 'complete' if successful else 'error'
        if successful:
            task.error = ''
        else:
            task.error = (
                'Azure Batch task returned non-zero or missing '
                'exit codes: {0}'.format(exit_codes)
            )
        task.save(update_fields=['status', 'error'])

        if not successful:
            request.status = 'Error'
            request.errors = task.error
            request.save(update_fields=['status', 'errors'])

        _finalize_verifier_request(request=request)

    except Exception as exc:
        _record_request_error(
            request=request,
            task=task,
            exception=exc,
            stage='Verifier Batch task completion'
        )

    finally:
        cleanup_complete = _delete_batch_resources(
            batch_client=batch_client,
            resource_id=job_id
        )
        if cleanup_complete:
            _clear_analysis_task_batch_ids(task)

def _finalize_verifier_request(*, request):
    """Create combined Verifier reports after all focused jobs terminate."""
    tasks = list(request.analysis_tasks.all())

    if any(item.status in ('pending', 'submitted') for item in tasks):
        return

    if any(item.status == 'error' for item in tasks):
        _handle_failed_verifier_run(verifier_request=request)
        return

    _handle_successful_verifier_run(
        batch_job_name=request.container_namer(),
        verifier_request=request
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
    verifier_request = VerifierRequest.objects.get(
        pk=task.verifier_request.pk
    )
    batch_job_name = verifier_request.container_namer()
    if not _check_tasks_completed(
        batch_client=batch_client, batch_job_name=batch_job_name
    ):
        return
    try:
        exit_codes_good = _check_exit_codes(
            batch_client=batch_client, batch_job_name=batch_job_name
        )
        if exit_codes_good:
            _handle_successful_verifier_run(
                batch_job_name=batch_job_name,
                verifier_request=verifier_request
            )
        else:
            _handle_failed_verifier_run(verifier_request=verifier_request)

        VerifierAzureRequest.objects.filter(id=task.id).delete()

    except Exception as exc:
        VerifierRequest.objects.filter(
            pk=task.verifier_request.pk
        ).update(status='Error', errors=exc)
        VerifierAzureRequest.objects.filter(id=task.id).delete()
    finally:
        _delete_batch_resources(
            batch_client=batch_client,
            resource_id=batch_job_name,
            pool_id=batch_job_name
        )


def _check_tasks_completed(
    *,
    batch_client,
    batch_job_name
):
    """Return whether all tasks in a Batch job completed."""
    try:
        cloud_tasks = list(batch_client.task.list(batch_job_name))
    except Exception as exc:
        logger.exception(
            "Unable to list tasks for Azure Batch job %s: %s",
            batch_job_name,
            str(exc),
        )
        return False

    if not cloud_tasks:
        logger.warning(
            "Azure Batch job %s currently has no tasks",
            batch_job_name,
        )
        return False

    for cloud_task in cloud_tasks:
        if cloud_task.state != batchmodels.TaskState.completed:
            return False

    return True


def _delete_batch_resources(*, batch_client, resource_id, pool_id=None):
    """Delete a Batch job and pool; return whether cleanup is complete."""
    if pool_id is None:
        pool_id = batch_pool_id_from_job_id(resource_id)

    cleanup_complete = True
    resources = (
        ('job', lambda: batch_client.job.delete(job_id=resource_id)),
        ('pool', lambda: batch_client.pool.delete(pool_id=pool_id))
    )

    for resource_type, delete in resources:
        try:
            delete()
        except Exception as exc:
            message = str(exc).lower()
            if (
                'does not exist' in message or
                'not found' in message or
                'marked for deletion' in message or
                'being reclaimed' in message
            ):
                continue

            cleanup_complete = False
            logger.warning(
                'Unable to delete Batch %s for %s: %s',
                resource_type,
                resource_id,
                str(exc)
            )

    return cleanup_complete


def _clear_analysis_task_batch_ids(task):
    """Stop historical cleanup polling after terminal deletion succeeds."""
    task.batch_job_id = ''
    task.batch_task_id = ''
    task.save(update_fields=['batch_job_id', 'batch_task_id'])

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
    verifier_request: VerifierRequest
):
    """
    Handle a successful PrimerVerifier run.

    Args:
        batch_job_name (str): The name of the batch job.
        verifier_request (VerifierRequest): The request object.
    """
    report_sas_url, summary_sas_url, run_folder = post_verifier_run(
        batch_job_name=batch_job_name,
        verifier_request=verifier_request
    )
    email_list = verifier_request.emails_array or []
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
    verifier_request: VerifierRequest
):
    """
    Handle a failed PrimerVerifier run.

    Args:
        verifier_request (VerifierRequest): The request object.
    """
    # Extract the email list from the request
    email_list = verifier_request.emails_array or []
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
    verifier_request: VerifierRequest
) -> Tuple[str, str, str]:
    """
    Post-run steps for the PrimerVerifier analysis.

    Args:
        batch_job_name (str): The name of the batch job.
        verifier_request (VerifierRequest): The VerifierRequest
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

        gene_reports = {}
        gene_report_folder = os.path.join(report_folder, 'gene')
        for root, unused_directories, filenames in os.walk(gene_report_folder):
            for filename in filenames:
                report_path = os.path.join(root, filename)
                relative_path = os.path.relpath(report_path, gene_report_folder)
                try:
                    with open(report_path, 'r', encoding='utf-8') as report_file:
                        gene_reports[relative_path] = report_file.read()
                except (IOError, UnicodeDecodeError):
                    continue
        verifier_request.gene_report = json.dumps(
            gene_reports, sort_keys=True
        )
        verifier_request.save(update_fields=['gene_report'])

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
        _prepare_validator_analysis(validator_request_pk=validator_request_pk)
    except Exception as exc:
        _handle_primer_error(
            exception=exc,
            request_model=ValidatorRequest,
            request_pk=validator_request_pk,
        )


def _validator_tasks(request):
    """Return the independent primer and optional gene task rows."""
    primer_task, unused_created = ValidatorAnalysisTask.objects.get_or_create(
        validator_request=request,
        task_type='primer',
    )
    gene_enabled = ValidatorTarget.objects.filter(
        sample__validator_request=request,
    ).filter(
        Q(gene_sequence__gt='') | Q(reference_fasta__gt='')
    ).exists()
    gene_task = None
    if gene_enabled:
        gene_task, unused_created = ValidatorAnalysisTask.objects.get_or_create(
            validator_request=request,
            task_type='gene',
        )
    return primer_task, gene_task


def _prepare_validator_analysis(*, validator_request_pk):
    """Prepare shared inputs and submit independent Validator jobs."""
    request = ValidatorRequest.objects.get(pk=validator_request_pk)

    _validate_request_panel_selections(request=request, panel_model=ValidatorPanel)

    container_name, primer_file_name = upload_primers(request=request)
    input_blob_names = [primer_file_name]
    if _validator_probe_records(request):
        upload_probe(request=request)
        input_blob_names.append('probe.fasta')
    input_blob_names.extend(_handle_panel_files(
        container_name=container_name,
        inclusivity=True,
        panel_model=ValidatorPanel,
        request_pk=request.pk,
    ))
    input_blob_names.extend(_handle_panel_files(
        container_name=container_name,
        inclusivity=False,
        panel_model=ValidatorPanel,
        request_pk=request.pk,
    ))
    if _upload_validator_gene_targets(
        container_name=container_name, request=request
    ):
        input_blob_names.append('targets/query.fasta')
    input_blobs = _generate_input_blob_sas_urls(
        container_name=container_name,
        blob_names=input_blob_names,
    )
    primer_vm_size, gene_vm_size, large_benchmark = _validator_vm_sizes(
        input_blob_names
    )
    logger.info(
        'Validator VM selection: request=%s large_ncbi_benchmark=%s '
        'primer_vm_size=%s gene_vm_size=%s',
        request.pk,
        large_benchmark,
        primer_vm_size,
        gene_vm_size,
    )
    primer_task, gene_task = _validator_tasks(request)
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
        vm_size=primer_vm_size,
    )
    if gene_task:
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
            vm_size=gene_vm_size,
        )


def _upload_validator_gene_targets(*, container_name, request):
    """Upload normalized gene references in GeneSeekr target FASTA format.

    Pasted and uploaded FASTA records are parsed with Biopython and rewritten
    with each sequence on a single line. This prevents inconsistent FASTA line
    wrapping from causing ``samtools faidx`` failures.
    """
    fasta_records = []
    seen_identifiers = set()

    def add_record(header, sequence, source_description):
        """Normalize and add one uniquely identified FASTA record."""
        header = str(header or "").strip()
        sequence = "".join(str(sequence or "").split()).upper()

        if header.startswith(">"):
            header = header[1:].strip()

        if not header:
            raise ValueError(
                "A gene reference from {0} has an empty FASTA header.".format(
                    source_description
                )
            )

        if not sequence:
            raise ValueError(
                "Gene reference {0} from {1} has no sequence.".format(
                    header, source_description
                )
            )

        invalid_bases = sorted(set(sequence).difference(set("ACGTN")))

        if invalid_bases:
            raise ValueError(
                "Gene reference {0} from {1} contains invalid bases: {2}.".format(
                    header, source_description, ", ".join(invalid_bases)
                )
            )

        header_parts = header.split(None, 1)
        identifier = header_parts[0]
        description = header_parts[1] if len(header_parts) > 1 else ""

        unique_identifier = identifier
        suffix = 2

        while unique_identifier in seen_identifiers:
            unique_identifier = "{0}__{1}".format(identifier, suffix)
            suffix += 1

        seen_identifiers.add(unique_identifier)

        if description:
            unique_header = "{0} {1}".format(unique_identifier, description)
        else:
            unique_header = unique_identifier

        fasta_records.append((unique_header, sequence))

    def parse_fasta_text(fasta_text, source_description):
        """Parse FASTA text and add all normalized records."""
        fasta_text = str(fasta_text or "").strip()

        if not fasta_text:
            raise ValueError(
                "The gene reference from {0} is empty.".format(source_description)
            )

        parsed_records = list(SeqIO.parse(StringIO(fasta_text), "fasta"))

        if not parsed_records:
            raise ValueError(
                "Unable to parse the gene FASTA from {0}.".format(source_description)
            )

        for record in parsed_records:
            add_record(
                header=record.description or record.id,
                sequence=str(record.seq),
                source_description=source_description,
            )

    targets = ValidatorTarget.objects.filter(
        sample__validator_request=request
    ).select_related("sample")

    for target in targets:
        source_description = "sample {0}, target {1}".format(
            target.sample.name, target.name
        )

        pasted_sequence = (target.gene_sequence or "").strip()

        if pasted_sequence:
            if pasted_sequence.startswith(">"):
                parse_fasta_text(
                    fasta_text=pasted_sequence, source_description=source_description
                )
            else:
                add_record(
                    header="{0}__{1}".format(target.sample.name, target.name),
                    sequence=pasted_sequence,
                    source_description=source_description,
                )

        reference_fasta = getattr(target, "reference_fasta", None)

        if reference_fasta:
            try:
                reference_fasta.open("rb")
                fasta_contents = reference_fasta.read()
            except (IOError, OSError) as exc:
                raise ValueError(
                    "Unable to read the uploaded gene FASTA for {0}: {1}".format(
                        source_description, str(exc)
                    )
                )
            finally:
                try:
                    reference_fasta.close()
                except Exception:
                    pass

            if isinstance(fasta_contents, bytes):
                try:
                    fasta_contents = fasta_contents.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(
                        "The uploaded gene FASTA for {0} is not "
                        "valid UTF-8: {1}".format(source_description, str(exc))
                    )

            parsed_records = list(SeqIO.parse(StringIO(fasta_contents), "fasta"))

            if not parsed_records:
                raise ValueError(
                    "The uploaded gene FASTA for {0} contains "
                    "no valid FASTA records.".format(source_description)
                )

            for record in parsed_records:
                add_record(
                    header="{0}__{1}__{2}".format(
                        target.sample.name, target.name, record.id
                    ),
                    sequence=str(record.seq),
                    source_description=source_description,
                )

    request_reference_fasta = getattr(request, "reference_fasta", None)

    if request_reference_fasta:
        try:
            request_reference_fasta.open("rb")
            fasta_contents = request_reference_fasta.read()
        except (IOError, OSError) as exc:
            raise ValueError(
                "Unable to read the request-level reference FASTA: {0}".format(str(exc))
            )
        finally:
            try:
                request_reference_fasta.close()
            except Exception:
                pass

        if isinstance(fasta_contents, bytes):
            try:
                fasta_contents = fasta_contents.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    "The request-level reference FASTA is not valid UTF-8: {0}".format(
                        str(exc)
                    )
                )

        parsed_records = list(SeqIO.parse(StringIO(fasta_contents), "fasta"))

        if not parsed_records:
            raise ValueError(
                "The request-level reference FASTA contains no valid FASTA records."
            )

        for record in parsed_records:
            if record.id.endswith(("-F", "-R")):
                continue

            add_record(
                header=record.description or record.id,
                sequence=str(record.seq),
                source_description=("the request-level reference FASTA"),
            )

    if not fasta_records:
        return False

    fasta_lines = []

    for header, sequence in fasta_records:
        fasta_lines.extend([">{0}".format(header), sequence])

    # Every sequence is written on one line, and the file ends with a
    # conventional newline. This prevents samtools faidx line-width errors.
    fasta_contents = "\n".join(fasta_lines) + "\n"

    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME, account_key=settings.AZURE_ACCOUNT_KEY
    )

    blob_client.create_blob_from_bytes(
        container_name=container_name,
        blob_name="targets/query.fasta",
        blob=fasta_contents.encode("utf-8"),
    )

    return True

def _normalize_gene_reports(gene_reports):
    """Normalize common GeneSeekr JSON/TSV/CSV report shapes."""
    normalized = []
    for report_path, contents in gene_reports.items():
        lower_path = report_path.lower()
        if lower_path.endswith('.json'):
            try:
                payload = json.loads(contents)
            except (TypeError, ValueError):
                continue
            records = payload if isinstance(payload, list) else [payload]
            for record in records:
                if isinstance(record, dict):
                    normalized.append(_normalize_gene_record(
                        report_path, record
                    ))
        elif lower_path.endswith(('.tsv', '.csv')):
            delimiter = '\t' if lower_path.endswith('.tsv') else ','
            for record in csv.DictReader(StringIO(contents), delimiter=delimiter):
                normalized.append(_normalize_gene_record(
                    report_path, record
                ))
    return [record for record in normalized if record]


def _upload_gene_category_report(blob_client, container_name, request,
                                 gene_reports):
    """Upload the inputs and decisions used for GeneSeekr categorization."""
    eligible_hits = []
    rejected_hits = []
    for hit in request.gene_results or []:
        try:
            identity = float(hit.get('identity'))
        except (TypeError, ValueError):
            identity = None
        if identity is not None and identity >= 90:
            eligible_hits.append(hit)
        else:
            rejected_hits.append(hit)
    category_counts = {}
    for values in (request.gene_exclusion_results or {}).values():
        for category in values:
            category_counts[category] = category_counts.get(category, 0) + 1
    payload = {
        'request_id': request.pk,
        'generated_at': datetime.datetime.utcnow().isoformat() + 'Z',
        'identity_cutoff_percent': 90,
        'summary': {
            'total_hits': len(request.gene_results or []),
            'eligible_hits': len(eligible_hits),
            'below_identity_cutoff_hits': len(rejected_hits),
            'category_counts': category_counts,
        },
        'blast_mode': getattr(request, 'blast_mode', None),
        'mismatch_tolerance': request.mismatches,
        'selected_categories': request.gene_exclusion_categories or [],
        'gene_reports': gene_reports,
        'normalized_hits': request.gene_results,
        'categories': request.gene_exclusion_results,
        'category_details': request.gene_exclusion_details,
    }
    blob_client.create_blob_from_bytes(
        container_name=container_name,
        blob_name='reports/gene_category_evaluation.json',
        # Python 3.5 cannot sort JSON dictionaries containing None and str
        # keys, which can occur in report-derived category data.
        blob=json.dumps(payload, sort_keys=False, indent=2).encode('utf-8'),
    )

    rows = StringIO()
    writer = csv.writer(rows, delimiter='\t', lineterminator='\n')
    writer.writerow([
        'report', 'target', 'category', 'reason', 'query', 'identity',
        'coverage', 'orientation', 'subject_start', 'subject_end',
        'subject_length', 'query_start', 'query_end', 'bit_score', 'gap',
        'paired_query', 'paired_identity', 'paired_subject_start',
        'paired_subject_end',
    ])
    for report, targets in (request.gene_exclusion_details or {}).items():
        for target, evidence_items in targets.items():
            for evidence in evidence_items:
                hit = evidence.get('hit') or evidence.get('first_hit') or {}
                paired_hit = evidence.get('second_hit') or {}
                writer.writerow([
                    report, target, evidence.get('category', ''),
                    evidence.get('reason', ''), hit.get('query', ''),
                    hit.get('identity', ''), hit.get('coverage', ''),
                    hit.get('orientation', ''), hit.get('subject_start', ''),
                    hit.get('subject_end', ''), hit.get('subject_length', ''),
                    hit.get('query_start', ''), hit.get('query_end', ''),
                    hit.get('bit_score', ''), evidence.get('gap', ''),
                    paired_hit.get('query', ''), paired_hit.get('identity', ''),
                    paired_hit.get('subject_start', ''),
                    paired_hit.get('subject_end', ''),
                ])
    blob_client.create_blob_from_bytes(
        container_name=container_name,
        blob_name='reports/gene_category_evaluation.tsv',
        blob=rows.getvalue().encode('utf-8'),
    )


def _normalize_gene_record(report_path, record):
    """Map GeneSeekr field-name variants to the Validator result contract."""
    def value(*names):
        for name in names:
            if name in record and record[name] not in ('', None):
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
            'percent_match'
        ),
        'coverage': value('coverage', 'Coverage', '% coverage', 'percent_coverage'),
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


def _submit_validator_task(*, request, analysis_task, command, vm_size):
    """Submit one Validator Batch job and persist its returned IDs."""
    job_name = '{0}-{1}'.format(
        request.container_namer(), analysis_task.task_type
    )
    submission = generic_api_submit(
        command=command,
        container_name=request.container_namer(),
        input_file_pattern=None,
        vm_size=vm_size,
        unique_id=analysis_task.task_type,
        log_prefix=analysis_task.task_type,
    )
    if not isinstance(submission, dict) or submission.get('status') != 'Success':
        raise RuntimeError('Validator Batch submission failed: {0}'.format(
            submission
        ))
    analysis_task.batch_job_id = submission.get('job_id') or job_name
    task_ids = submission.get('tasks') or []
    analysis_task.batch_task_id = task_ids[0] if task_ids else ''
    analysis_task.status = 'submitted'
    analysis_task.error = ''
    analysis_task.save(update_fields=[
        'batch_job_id', 'batch_task_id', 'status', 'error'
    ])


def _create_validator_primer_task_command(
    *, request, container_name, file_name, input_blobs
):
    """Build the existing PrimerValidator command for its own Batch job."""
    path = '$AZ_BATCH_NODE_MOUNTS_DIR/{0}'.format(container_name)
    return _create_primer_cmd(
        container_name='{0}-primer'.format(container_name),
        file_name=file_name,
        input_blobs=input_blobs,
        path=path,
        request=request,
    )


def _create_validator_gene_task_command(
    *, request, container_name, input_blobs
):
    """Build a separate GeneSeekr command using the shared input container."""
    job_name = '{0}-gene'.format(container_name)
    run_directory = '/datadrive/{0}'.format(job_name)
    mount_dir = '$AZ_BATCH_NODE_MOUNTS_DIR/{0}'.format(container_name)
    sequences = posixpath.join(run_directory, 'sequences')
    targets = posixpath.join(run_directory, 'targets')
    reports = posixpath.join(run_directory, 'reports')
    parts = [
        'source $CONDA/activate /envs/cowbat',
        'set -euo pipefail',
        'mkdir -p {0} {1} {2}'.format(
            shlex.quote(sequences), shlex.quote(targets), shlex.quote(reports)
        ),
    ]
    for item in input_blobs:
        destination = posixpath.join(run_directory, item['blob_name'])
        parts.extend([
            'mkdir -p {0}'.format(shlex.quote(posixpath.dirname(destination))),
            'curl --fail --location --retry 5 --output {0} {1}'.format(
                shlex.quote(destination), shlex.quote(item['sas_url'])
            ),
        ])
    parts.extend([
        'for archive in {0}/inclusivity/*.zip {0}/exclusivity/*.zip; do '
        '[ -e "$archive" ] || continue; unzip -n -q "$archive" -d {1}; done'.format(
            run_directory, shlex.quote(sequences)
        ),
        'GeneSeekr blastn -u -s {0} -t {1} -r {2}'.format(
            shlex.quote(sequences), shlex.quote(targets), shlex.quote(reports)
        ),
        'mkdir -p {0}/reports/gene'.format(mount_dir),
        'cp -R {0}/. {1}/reports/gene/'.format(
            shlex.quote(reports), mount_dir
        ),
    ])
    return ' && '.join(parts)


@shared_task
def check_validator_tasks():
    """
    Check the status of ValidatorAzureRequest tasks. If a task fails, perform
    clean-up. If a task succeeds, perform necessary steps and clean-up.
    """
    # Get all active independent Validator analysis tasks.
    validator_tasks = ValidatorAnalysisTask.objects.filter(
        status='submitted'
    )
    legacy_tasks = ValidatorAzureRequest.objects.all()

    # Set up the credentials
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )

    # Create the batch client
    batch_client = batch.BatchServiceClient(
        credentials, base_url=settings.BATCH_ACCOUNT_URL
    )

    # Process each independent task.
    for task in validator_tasks:
        _process_validator_analysis_task(
            batch_client=batch_client,
            task=task
        )
    # Retry cleanup for terminal tasks whose previous cleanup was transiently
    # unsuccessful.
    terminal_tasks = ValidatorAnalysisTask.objects.filter(
        status__in=('complete', 'error')
    ).exclude(batch_job_id='')
    for task in terminal_tasks:
        cleanup_complete = _delete_batch_resources(
            batch_client=batch_client,
            resource_id=task.batch_job_id
        )
        if cleanup_complete:
            _clear_analysis_task_batch_ids(task)
    for task in legacy_tasks:
        _process_validator_task(
            batch_client=batch_client,
            task=task
        )


def _process_validator_analysis_task(*, batch_client, task):
    """Poll one independent Validator job and finalize when both finish."""
    request = task.validator_request
    job_id = task.batch_job_id
    pool_id = batch_pool_id_from_job_id(job_id)

    recovery = retry_failed_pool_resize(
        batch_client=batch_client,
        pool_id=pool_id,
        target_dedicated_nodes=1,
        target_low_priority_nodes=0
    )
    recovery_action = recovery.get('action')

    if recovery_action == 'resize_retried':
        logger.warning(
            'Validator task waiting after pool resize retry: '
            'request=%s task=%s pool=%s',
            request.pk,
            task.task_type,
            pool_id
        )
        return

    if recovery_action in ('retry_throttled', 'resize_in_progress'):
        return

    if recovery_action in (
        'non_retryable_resize_error',
        'inspection_failed',
        'resize_request_failed',
        'unsupported_allocation_state'
    ):
        logger.error(
            'Validator Batch pool recovery requires attention: '
            'request=%s task=%s pool=%s result=%s',
            request.pk,
            task.task_type,
            pool_id,
            recovery
        )

    if not _check_tasks_completed(
        batch_client=batch_client,
        batch_job_name=job_id
    ):
        return

    try:
        exit_codes = _batch_task_exit_codes(
            batch_client=batch_client,
            batch_job_name=job_id
        )
        successful = bool(exit_codes) and all(
            item['exit_code'] == 0
            for item in exit_codes
        )

        task.status = 'complete' if successful else 'error'
        if successful:
            task.error = ''
        else:
            task.error = (
                'Azure Batch task returned non-zero or missing '
                'exit codes: {0}'.format(exit_codes)
            )
        task.save(update_fields=['status', 'error'])

        if not successful:
            request.status = 'Error'
            request.errors = task.error
            request.save(update_fields=['status', 'errors'])

        _finalize_validator_request(request=request)

    except Exception as exc:
        _record_request_error(
            request=request,
            task=task,
            exception=exc,
            stage='Validator Batch task completion'
        )

    finally:
        cleanup_complete = _delete_batch_resources(
            batch_client=batch_client,
            resource_id=job_id
        )
        if cleanup_complete:
            _clear_analysis_task_batch_ids(task)

def _finalize_validator_request(*, request):
    """Create the combined Validator reports after all jobs terminate."""
    tasks = list(request.analysis_tasks.all())
    if any(item.status in ('pending', 'submitted') for item in tasks):
        return
    if any(item.status == 'error' for item in tasks):
        _handle_failed_validator_run(validator_request=request)
        return
    _handle_successful_validator_run(
        batch_job_name=request.container_namer(),
        validator_request=request,
    )


def _process_validator_task(
    *,  # Force the use of keyword arguments
    batch_client: batch.BatchServiceClient,
    task: ValidatorAzureRequest
):
    """
    Process an individual validator task.

    Args:
        batch_client (batch.BatchServiceClient): The Azure Batch client.
        task (ValidatorAzureRequest): The task to process.
    """
    validator_request = ValidatorRequest.objects.get(
        pk=task.validator_request.pk
    )
    batch_job_name = validator_request.container_namer()
    if not _check_tasks_completed(
        batch_client=batch_client, batch_job_name=batch_job_name
    ):
        return
    try:
        exit_codes_good = _check_exit_codes(
            batch_client=batch_client, batch_job_name=batch_job_name
        )
        if exit_codes_good:
            _handle_successful_validator_run(
                batch_job_name=batch_job_name,
                validator_request=validator_request
            )
        else:
            _handle_failed_validator_run(
                validator_request=validator_request
            )

        ValidatorAzureRequest.objects.filter(id=task.id).delete()

    except Exception as exc:
        _record_request_error(
            request=validator_request,
            exception=exc,
            stage='post-processing validator reports',
        )
        ValidatorAzureRequest.objects.filter(id=task.id).delete()
    finally:
        _delete_batch_resources(
            batch_client=batch_client,
            resource_id=batch_job_name,
            pool_id=batch_job_name
        )


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

        gene_reports = {}
        gene_report_folder = os.path.join(report_folder, 'gene')
        for root, unused_directories, filenames in os.walk(gene_report_folder):
            for filename in filenames:
                report_path = os.path.join(root, filename)
                relative_path = os.path.relpath(report_path, gene_report_folder)
                try:
                    with open(report_path, 'r', encoding='utf-8') as report_file:
                        gene_reports[relative_path] = report_file.read()
                except (IOError, UnicodeDecodeError):
                    continue
        validator_request.gene_report = json.dumps(
            gene_reports, sort_keys=True
        )
        validator_request.gene_results = _normalize_gene_reports(gene_reports)
        validator_request.validator_original_report = validator_request.report
        salmonella_classification = validator_request.samples.filter(
            classification_rule='salmonella'
        ).exists()
        category_targets = ('inva', 'stn') if salmonella_classification else None
        categories = categorize_gene_hits(
            validator_request.gene_results,
            allowed_targets=category_targets,
        )
        category_details = categorize_gene_hit_details(
            validator_request.gene_results,
            allowed_targets=category_targets,
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
            'gene_report', 'gene_results', 'validator_original_report',
            'gene_exclusion_results', 'gene_exclusion_details',
            'gene_exclusion_note', 'report'
        ])

        _upload_gene_category_report(
            blob_client=blob_client,
            container_name=batch_job_name,
            request=validator_request,
            gene_reports=gene_reports,
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
        error_text = _format_task_error(
            exc, 'post_validator_run: {0}'.format(batch_job_name)
        )
        logger.error(error_text)
        print(error_text)
        raise
