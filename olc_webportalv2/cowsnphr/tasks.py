"""Tasks for the COWSNPhR app."""

# Standard imports
import fnmatch
import logging
import os
import re
import shutil
from time import sleep

# Third-party imports
import pandas as pd

# Celery Task Management
from celery import shared_task
from django.core.files.uploadedfile import UploadedFile

# Django imports
from django.db import DatabaseError
from django.utils.translation import gettext

# Sentry
from sentry_sdk import capture_exception

from olc_webportalv2.common.batch_client import BatchServiceClient

# Portal-specific imports
from olc_webportalv2.common.methods import (
    create_batch_client,
    generic_api_submit,
)
from olc_webportalv2.common.storage_client import (
    StorageServiceError,
    get_storage_client,
)
from olc_webportalv2.cowsnphr.models import (
    ContainerName,
    COWSNPhRAzureTask,
    COWSNPhRRequest,
)
from olc_webportalv2.filezone.methods import FileLocate
from olc_webportalv2.primer_finder.methods import send_email

logger = logging.getLogger(__name__)


def find_containers():
    """Find sequencing-run and COWSNPhR containers.

    Exclude output containers.

    """
    containers = get_storage_client().list_containers()
    # Compile the regular expressions
    seq_run_re = re.compile(r"\d{6}")
    cowsnphr_re = re.compile("cowsnphr")
    # Use a list comprehension to find the matching container names
    run_set = {
        container.name
        for container in containers
        if (
            (
                seq_run_re.match(container.name)
                or cowsnphr_re.match(container.name)
            )
            and "-output" not in container.name
        )
    }
    return sorted(run_set)


@shared_task
def refresh_container_names():
    """Update the container names in the model."""
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


def container_sanity(container_name: str) -> tuple[bool, bool, str]:
    """Check that a container has required FASTQ, reference, and mask files.

    Args:
        container_name: Name of the container to inspect.

    Returns:
        Flags indicating whether query and reference files are present, plus
        the optional mask file path.

    """
    # Create a client for listing blobs in the container
    blob_client = get_storage_client()
    # List all the blobs in the container
    blobs = blob_client.list_objects(container_name)
    # Create variables to store whether the query files, the reference genome,
    # and the optional mask file are present
    fastq = False
    ref = False
    mask = ""
    # Check for all required inputs in a single pass.
    for blob in blobs:
        if fnmatch.fnmatch(blob.name, os.path.join("fastq", "*.fastq.gz")):
            fastq = True
        if fnmatch.fnmatch(blob.name, os.path.join("ref", "*.fasta")):
            ref = True
        if fnmatch.fnmatch(blob.name, os.path.join("ref", "*.bed")):
            mask = os.path.join("ref", os.path.basename(blob.name))
    return fastq, ref, mask


def upload_files_for_cowsnphr(file: UploadedFile, container_name: str) -> None:
    """Upload an analysis input file to the appropriate container folder.

    Args:
        file: Uploaded sequence or reference file.
        container_name: Destination blob container.

    """
    # Create a blob client
    blob_client = get_storage_client()
    # Create the container (if required)
    blob_client.ensure_container(container_name)
    # Determine if the file extension is .gz and set the destination folder
    # appropriately
    if os.path.splitext(file.name)[-1] == ".gz":
        folder = "fastq"
    else:
        folder = "ref"
    # Create a variable to store the blob file name and path
    blob_name = os.path.join(folder, file.name)
    # Upload the file to blob
    blob_client.upload_bytes(
        container_name,
        blob_name,
        file.read(),
        content_type=getattr(file, "content_type", None),
    )


def create_system_call(container_name: str, mask: str) -> str:
    """Build the COWSNPhR command submitted to the Batch VM.

    Args:
        container_name: Container holding the input files.
        mask: Optional path to the mask file within the container.

    Returns:
        Shell command that stages inputs, runs COWSNPhR, and copies outputs.

    """
    # Copy the files to /datadrive, perform the analyses
    # there, and move everything back once the pipeline is complete

    # Create a variable to store the extremely long path information
    path = f"$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}"

    # Define the path variables
    fastq_path = os.path.join("/datadrive", container_name, "fastq")
    ref_path = os.path.join("/datadrive", container_name, "ref")

    # Create the system call. Activate the conda environment, create the
    # datadrive folder, copy the files to it, and run the analyses. Use the -g
    # flag to enable --gpus and the -w flag to specify the working directory.
    # Then copy everything back to the container
    cmd = (
        "source $CONDA/activate /envs/cowsnphr && "
        f"mkdir -p /datadrive/{container_name} && "
        f"cp -R {path} /datadrive/ && "
        f"cowsnphr -s {fastq_path} "
        f"-r {ref_path} -g -w /datadrive"
    )
    # Update the command if the mask file was provided
    if mask:
        cmd += f" -m /datadrive/{container_name}/{mask}"

    # Update the command with the final steps to copy the files back from the
    # datadrive to the container
    cmd += f" && cp -R /datadrive/{container_name} $AZ_BATCH_NODE_MOUNTS_DIR"
    return cmd


@shared_task
def run_cowsnphr_batch(primary_key: int):
    """Prepare inputs and submit a COWSNPhR request to Batch.

    Args:
        primary_key: Primary key of the request to process.

    """
    cowsnphr = COWSNPhRRequest.objects.get(pk=primary_key)
    # If a list of SEQIDs has been provided, locate and copy the necessary
    # files to the analysis-specific blob container
    if cowsnphr.seqids:
        ref = find_ref(cowsnphr=cowsnphr)
        queries = find_query(cowsnphr=cowsnphr)
        # Do not continue if there were errors locating files
        if cowsnphr.seqid_errors or ref is None or queries is None:
            return
        # Copy the blobs to the destination container
        copy_blobs(
            blob_metadata_list=ref,
            destination_container=cowsnphr.container_name,
            destination_path="ref",
        )
        copy_blobs(
            blob_metadata_list=queries,
            destination_container=cowsnphr.container_name,
            destination_path="fastq",
        )
    # Ensure that the necessary files are present
    fastq, ref, mask = container_sanity(container_name=cowsnphr.container_name)
    if not fastq or not ref:
        if not fastq:
            cowsnphr.error_list.append(
                'FASTQ files could not be located in the "fastq" folder of '
                f"the supplied container: {cowsnphr.container_name}"
            )
        if not ref:
            cowsnphr.error_list.append(
                'A FASTQ-formatted genome could not be located in the "ref" '
                f"folder of the supplied container: {cowsnphr.container_name}"
            )
        cowsnphr.status = "Error"
        cowsnphr.save()
        return
    # Create the system call
    cmd = create_system_call(mask=mask, container_name=cowsnphr.container_name)

    # Create a folder into which any error code files are to be written
    local_folder = os.path.join("olc_webportalv2", "media", str(cowsnphr))
    os.makedirs(local_folder, exist_ok=True)

    try:
        # Submit the command to the AzureBatch service
        generic_api_submit(
            command=cmd,
            container_name=cowsnphr.container_name,
            vm_size="Standard_NV18ads_A10_v5",
            analysis_type="COWSNPhR",
            unique_id="FoodPort",
        )

        # Create the AzureTask database entry
        COWSNPhRAzureTask.objects.create(
            cowsnphr=cowsnphr,
            exit_code_file=os.path.join(local_folder, "exit_codes.txt"),
        )
    except Exception as exc:
        file_handler = logging.FileHandler(os.path.join(local_folder, "error"))
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        try:
            logger.exception(
                "Could not submit COWSNPhR request %s", cowsnphr.pk
            )
        finally:
            logger.removeHandler(file_handler)
            file_handler.close()
        capture_exception(exc)
        cowsnphr.status = "Error"
        cowsnphr.error_list.append(str(exc))
        cowsnphr.save()


def find_ref(cowsnphr: COWSNPhRRequest):
    """Locate the reference genome selected for an analysis.

    Args:
        cowsnphr: Request containing the reference SEQID.

    Returns:
        Matching blob metadata, or ``None`` if no reference is found.

    """
    # Attempt to locate the reference file in the "processed-data" container
    # first
    file_obj = FileLocate(
        container_regex=["processed-data"],
        container_exclude_regex=[],
        file_regex=[f"{cowsnphr.ref}.fasta"],
        file_exclude_regex=[],
        debug=True,
    )
    file_matches, _ = file_obj.main()
    if file_matches:
        ref = file_matches["processed-data"]
        return ref
    # Search all containers if a reference was not found in "processed-data"
    file_obj = FileLocate(
        container_regex=["*"],
        container_exclude_regex=[],
        file_regex=[f"{cowsnphr.ref}.fasta"],
        file_exclude_regex=[],
        debug=True,
    )
    file_matches, _ = file_obj.main()
    # Update the database entry with an error that the reference file SEQID
    # could not be located
    if not file_matches:
        cowsnphr.seqid_errors.append(
            gettext(
                f"Could not locate a FASTA file for the supplied reference "
                f"genome {cowsnphr.ref}"
            )
        )
        cowsnphr.status = "Error"
        cowsnphr.save()
        return None
    # Try to use a reference in the "BestAssemblies" folder
    for _, ref_list in file_matches.items():
        for ref_dict in ref_list:
            if "BestAssemblies" in ref_dict["blob_name"]:
                return [ref_dict]
    # If there are no references in a "BestAssemblies" folder, select the
    # first item
    container = next(iter(file_matches))
    ref = file_matches[container]
    return ref


def find_query(cowsnphr: COWSNPhRRequest) -> list[dict] | None:
    """Locate paired FASTQ files for every query SEQID.

    Args:
        cowsnphr: Request containing the query SEQIDs.

    Returns:
        Blob metadata for all query files, or ``None`` if files are missing.

    """
    # Attempt to locate the query files
    query_list = [f"{seqid}*.fastq.gz" for seqid in cowsnphr.seqids]
    file_obj = FileLocate(
        container_regex=["*"],
        container_exclude_regex=[
            "output",
            "ampliseq",
            "amrsummary",
            "cowbat",
            "geneseekr",
            "primer",
            "mash",
            "neighbor",
            "tree",
            "vir-typer",
            "fake",
        ],
        file_regex=query_list,
        file_exclude_regex=["trimmed", "baited"],
        debug=True,
    )
    file_matches, _ = file_obj.main()
    # If no files were returned, update the database with the error
    if not file_matches:
        cowsnphr.seqid_errors.append(
            gettext(
                f"Could not locate .fastq.gz files for the supplied query "
                f"genomes {','.join(cowsnphr.seqids)}"
            )
        )
        cowsnphr.status = "Error"
        cowsnphr.save()
        return None
    # Store all matched FASTQ files.
    seqids = []
    # Initialise a dictionary to track the presence/absence of forward and
    # reverse reads
    presence_dict = {}
    # Add the forward and reverse reads presence for each SEQID
    for seqid in cowsnphr.seqids:
        presence_dict[seqid] = {"R1": False, "R2": False}
    # Iterate over all the matches
    for _, file_list in file_matches.items():
        for file_dict in file_list:
            for seqid in cowsnphr.seqids:
                if seqid in file_dict["blob_name"]:
                    # Update the presence dictionary
                    if "R1" in file_dict["blob_name"]:
                        presence_dict[seqid]["R1"] = True
                    if "R2" in file_dict["blob_name"]:
                        presence_dict[seqid]["R2"] = True
                    # Add each file only once.
                    if file_dict not in seqids:
                        seqids.append(file_dict)
    # Initialise a variable to track whether all the necessary query files
    # were located
    errors = False
    for seqid, presence in presence_dict.items():
        # Check if either the forward or reverse reads are missing
        if presence["R1"] is False or presence["R2"] is False:
            cowsnphr.seqid_errors.append(
                gettext(
                    f"Could not locate one or more .fastq.gz files for "
                    f"SEQID {seqid}"
                )
            )
            cowsnphr.status = "Error"
            cowsnphr.save()
            errors = True
    # Return None if any files were missing
    if errors:
        return None
    return seqids


def copy_blobs(
    blob_metadata_list: list, destination_container: str, destination_path: str
) -> None:
    """Copy blob objects into a nested folder in a destination container.

    Args:
        blob_metadata_list: Metadata dictionaries for the source blobs.
        destination_container: Container receiving the copied objects.
        destination_path: Folder prefix for the copied objects.

    """
    blob_service = get_storage_client()
    blob_service.ensure_container(destination_container)
    for blob_metadata_dict in blob_metadata_list:
        # Set the new name of the blob as the destination folder and the file
        # name (without path information )
        blob_name = os.path.join(
            destination_path, os.path.basename(blob_metadata_dict["blob_name"])
        )
        blob_service.copy_object(
            blob_metadata_dict["container_name"],
            blob_metadata_dict["blob_name"],
            destination_container,
            blob_name,
        )


def check_for_task_completion(
    task: COWSNPhRAzureTask,
    batch_client: BatchServiceClient,
) -> tuple[bool, COWSNPhRRequest]:
    """Check Batch task state and recover from an unusable node or pool.

    Args:
        task: Database record for the submitted Batch task.
        batch_client: Client used to query and manage Batch resources.

    Returns:
        Completion status and the associated COWSNPhR request.

    """
    # Retrieve the COWSNPhRRequest object corresponding to the
    # cowsnphr_request primary key
    cowsnphr_request = COWSNPhRRequest.objects.get(pk=task.cowsnphr.pk)
    # Set the container name appropriately
    batch_job_name = cowsnphr_request.container_name
    try:
        cloud_tasks = list(batch_client.task.list(batch_job_name))
        if not cloud_tasks:
            logger.info(
                "No Batch tasks found yet for COWSNPhR job %s",
                batch_job_name,
            )
            return False, cowsnphr_request

        tasks_completed = all(
            cloud_task.state == "completed" for cloud_task in cloud_tasks
        )
        logger.debug(
            "COWSNPhR Batch tasks for %s completed: %s",
            batch_job_name,
            tasks_completed,
        )
        # Return if the tasks are complete
        if tasks_completed:
            return tasks_completed, cowsnphr_request

        # Locate all the batch pools
        pools = batch_client.pool.list()

        # Iterate over the pools
        for pool in pools:
            logger.debug(
                "Checking Batch pool %s for COWSNPhR job %s",
                pool.id,
                batch_job_name,
            )
            # Ensure that the current batch job is being evaluated
            if pool.id != batch_job_name:
                continue

            # List all the nodes in the pool
            nodes = batch_client.compute_node.list(pool.id)
            for node in nodes:
                logger.debug("Batch node %s state: %s", node.id, node.state)
                # Reboot the node if it becomes unusable
                if node.state == "unusable":
                    logger.warning(
                        "Rebooting Batch node %s due to unusable state",
                        node.id,
                    )
                    batch_client.compute_node.reboot(
                        pool_id=pool.id,
                        node_id=node.id,
                        node_reboot_option="requeue",
                    )
                    return False, cowsnphr_request

            # Proceed if there were batch resize errors. These usually
            # happen due to the quota getting reached
            if not pool.resize_errors:
                return tasks_completed, cowsnphr_request

            # Delete the pool, job, and task
            delete_pool_job(
                batch_client=batch_client, batch_job_name=batch_job_name
            )
            # Give the pool a chance to be deleted
            sleep(30)

            # Retry!
            # Set the local folder path
            local_folder = os.path.join(
                "olc_webportalv2", "media", batch_job_name
            )
            try:
                # Ensure that the necessary files are present
                _, _, mask = container_sanity(
                    container_name=cowsnphr_request.container_name
                )

                # Create the system call
                cmd = create_system_call(
                    mask=mask, container_name=cowsnphr_request.container_name
                )

                # Resubmit the batch request
                generic_api_submit(
                    command=cmd,
                    container_name=cowsnphr_request.container_name,
                    vm_size="Standard_NV18ads_A10_v5",
                    analysis_type="COWSNPhR",
                    unique_id="FoodPort",
                )

                # Delete the task
                task.delete()

                # Recreate the task
                COWSNPhRAzureTask.objects.create(
                    cowsnphr=cowsnphr_request,
                    exit_code_file=os.path.join(
                        local_folder, "exit_codes.txt"
                    ),
                )
            except Exception as exc:
                file_handler = logging.FileHandler(
                    os.path.join(local_folder, "error")
                )
                file_handler.setLevel(logging.INFO)
                logger.addHandler(file_handler)
                try:
                    logger.exception(
                        "Could not resubmit COWSNPhR request %s",
                        cowsnphr_request.pk,
                    )
                finally:
                    logger.removeHandler(file_handler)
                    file_handler.close()
                capture_exception(exc)
                cowsnphr_request.status = "Error"
                cowsnphr_request.error_list.append(str(exc))
                cowsnphr_request.save()
    # If something errors first time through, jobs can't get deleted. In that
    # case, give up.
    except Exception as exc:
        cowsnphr_request.status = "Error"
        cowsnphr_request.error_list.append(str(exc))
        cowsnphr_request.save()
        # Delete task, so we don't keep iterating over it.
        COWSNPhRAzureTask.objects.filter(pk=task.pk).delete()

    return tasks_completed, cowsnphr_request


def delete_pool_job(
    batch_client: BatchServiceClient, batch_job_name: str
) -> bool:
    """Delete a Batch job and pool after checking task exit codes.

    Args:
        batch_client: Client used to query and delete Batch resources.
        batch_job_name: Identifier shared by the Batch job and pool.

    Returns:
        Whether every task completed with exit code zero.

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
    """Mark a request complete and notify subscribed email addresses.

    Args:
        cowsnphr: Completed analysis request.

    """
    cowsnphr.status = "Complete"
    cowsnphr.save()
    # Send emails
    for email in cowsnphr.emails_array or []:
        send_email(
            subject=f'COWSNPhR Analysis "{cowsnphr.project_name}" Complete',
            body=(
                f"Dear {cowsnphr.user},\n\n"
                f'Your COWSNPhR analysis, "{cowsnphr.project_name}", is '
                "complete.\n\n"
                "Reports are available for "
                f"download: {cowsnphr.archive_download_link}\n\n"
                "Best regards,\n"
                "The FoodPort development team"
            ),
            recipient=email,
        )


def task_failed(cowsnphr):
    """Mark a request failed and notify subscribed email addresses.

    Args:
        cowsnphr: Failed analysis request.

    """
    # Send emails
    errors = "\n".join(cowsnphr.error_list or []) or "None"
    for email in cowsnphr.emails_array or []:
        send_email(
            subject=f'COWSNPhR Analysis "{cowsnphr.project_name}" Failed',
            body=(
                f"Dear {cowsnphr.user},\n"
                f'Your COWSNPhR analysis, "{cowsnphr.project_name}", '
                "has failed.\n"
                f"The following errors were recorded: {errors}\n\n"
                "Sorry for the inconvenience,\n"
                "The FoodPort development team"
            ),
            recipient=email,
        )
    # Update the model with the error status
    cowsnphr.status = "Error"
    cowsnphr.save()


@shared_task
def check_cowsnphr_tasks():
    """Poll active Batch tasks and run their completion workflows."""
    # Create a batch client
    batch_client = create_batch_client()

    # Retrieve all COWSNPhRAzureTask objects (they should be deleted after
    # they finish, so anything retrieved should be active)
    cowsnphr_tasks = COWSNPhRAzureTask.objects.filter()

    # Iterate over all the tasks to see if they are complete
    for task in cowsnphr_tasks:
        task_completed, cowsnphr = check_for_task_completion(
            task=task, batch_client=batch_client
        )

        # Allow the task to complete
        if not task_completed:
            continue

        # Clean up the job and pool if the task is complete
        exit_codes_good = delete_pool_job(
            batch_client=batch_client, batch_job_name=cowsnphr.container_name
        )

        # Perform appropriate actions depending on whether or not the task was
        # successful
        if exit_codes_good:
            post_processing(cowsnphr=cowsnphr)
            task_succeeded(cowsnphr=cowsnphr)
        else:
            task_failed(cowsnphr=cowsnphr)

        # Delete the COWSNPhRAzureTask
        COWSNPhRAzureTask.objects.filter(pk=task.pk).delete()


def post_processing(cowsnphr):
    """Create the output archive and generate its download link.

    Args:
        cowsnphr: Database entry for the current COWSNPhR analysis.

    """
    # Download the report folders to the local system
    blob_client, local_folder, output_container = download_folders(
        cowsnphr=cowsnphr
    )
    # Load the reports into the COWSNPhR ReportOutputs model
    populate_models(cowsnphr=cowsnphr, local_folder=local_folder)

    # Create an archive of the reports
    archive_file = compress_outputs(
        cowsnphr=cowsnphr, local_folder=local_folder
    )

    # Upload the archive to blob storage
    sas_url = generate_archive_download_link(
        blob_client=blob_client,
        container_name=output_container,
        output_zipfile=archive_file,
    )

    # Update the model with the link
    cowsnphr.archive_download_link = sas_url
    cowsnphr.save()

    # Remove the folder storing the outputs
    shutil.rmtree(local_folder)


def download_folders(cowsnphr):
    """Download analysis reports from blob storage.

    Args:
        cowsnphr: Request whose output container contains the reports.

    Returns:
        Storage client, local report directory, and output container name.

    """
    # Create a client for listing blobs in the container
    blob_client = get_storage_client()
    # Output container name is the container name
    output_container = cowsnphr.container_name
    # Set the name of the local folder
    local_folder = os.path.join(
        "olc_webportalv2", "media", cowsnphr.project_name
    )
    os.makedirs(local_folder, exist_ok=True)
    # List all the blobs in the container
    blobs = blob_client.list_objects(output_container)
    # Store the names of the desired folders in a list
    desired_folders = [
        "alignments",
        "snv_matrix",
        "summary_tables",
        "tree_files",
        "vcf_files",
    ]

    # Iterate over all the desired folder names
    for folder in desired_folders:
        for blob in blobs:
            if fnmatch.fnmatch(blob.name, os.path.join("fastq", folder, "*")):
                os.makedirs(os.path.join(local_folder, folder), exist_ok=True)
                blob_client.download_file(
                    output_container,
                    blob.name,
                    os.path.join(
                        local_folder, folder, os.path.basename(blob.name)
                    ),
                )
    return blob_client, local_folder, output_container


def populate_models(cowsnphr, local_folder):
    """Parse downloaded reports and save their data to the request.

    Args:
        cowsnphr: Request receiving the parsed report data.
        local_folder: Directory containing downloaded report files.

    """
    # Alignment
    alignment_file = os.path.join(
        local_folder, "alignments", "alignment.fasta"
    )
    alignment = str()
    with open(alignment_file, encoding="utf-8") as alignment_handle:
        for line in alignment_handle:
            alignment += line.replace("\n", "<br>")

    # Amino acid summary table
    aa_summary_file = os.path.join(
        local_folder, "summary_tables", "aa_snv_sorted_table.xlsx"
    )
    aa_summary_table, aa_headers, aa_header_padding = read_spreadsheet(
        report=aa_summary_file,
        excel=True,
    )
    # Assembly Report
    assembly_file = os.path.join(
        local_folder, "summary_tables", "assembly_report.tsv"
    )
    assembly_report, assembly_headers, _ = read_spreadsheet(
        report=assembly_file
    )
    # Contig Summary
    contig_file = os.path.join(
        local_folder, "summary_tables", "contig_summary.tsv"
    )
    contig_summary, contig_headers, _ = read_spreadsheet(
        report=contig_file,
        index_col=False,
    )
    # Nucleotide summary table
    nt_summary_file = os.path.join(
        local_folder, "summary_tables", "nt_snv_sorted_table.xlsx"
    )
    nt_summary_table, nt_headers, nt_header_padding = read_spreadsheet(
        report=nt_summary_file,
        excel=True,
    )
    # Phylogenetic tree
    tree_file = os.path.join(local_folder, "tree_files", "best_tree.tre")
    phylogenetic_tree = str()
    with open(tree_file, encoding="utf-8") as tree_handle:
        for line in tree_handle:
            phylogenetic_tree += line.replace("\n", "<br>")
    # SNV Matrix
    matrix_file = os.path.join(local_folder, "snv_matrix", "snv_matrix.tsv")
    snv_matrix, snv_matrix_headers, _ = read_spreadsheet(report=matrix_file)
    # SNV Summary
    snv_summary_file = os.path.join(
        local_folder, "summary_tables", "snv_summary.tsv"
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


def read_spreadsheet(report: str, excel: bool = False, index_col: bool = True):
    """Read a tab-separated or Excel report into table data.

    Args:
        report: Path to the report file.
        excel: Whether the report is an Excel workbook.
        index_col: Whether CSV parsing uses the first column as its index.

    Returns:
        Table rows, column headers, and optional Excel header padding.

    """
    # Use pandas to read in the report
    if excel:
        report_dataframe = pd.read_excel(
            report,
            index_col=False,
        ).fillna("")
    else:
        if not index_col:
            report_dataframe = pd.read_csv(
                report,
                sep="\t",
                index_col=False,
            ).fillna("")
        else:
            report_dataframe = pd.read_csv(
                report, sep="\t", skip_blank_lines=True
            ).fillna("")
    # Remove any "Unnamed: X" cells
    if not excel:
        for iterator in range(1000):
            try:
                report_dataframe.drop(
                    columns=f"Unnamed: {iterator}", inplace=True
                )
            except KeyError:
                pass
    # Extract the headers from the dataframe
    if excel:
        report_headers = [
            column
            for column in report_dataframe.columns
            if "Unnamed" not in column
        ]
        header_padding = []
        for integer in range(
            (len(list(report_dataframe.columns)) - len(report_headers))
        ):
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
        # Iterate over the row number and the column data list for each row
        for pk, data_list in report_dataframe.items():
            # Initialise the row number in the dictionary as requied
            if pk not in report_dict:
                report_dict[pk] = []
            # Add each value in the list of columns to the list
            for value in data_list:
                report_dict[pk].append(value)
    return report_dict, report_headers, header_padding


def compress_outputs(cowsnphr, local_folder):
    """Create a zip archive containing the analysis output folders.

    Args:
        cowsnphr: Request whose project name and ID label the archive.
        local_folder: Directory containing the output files to archive.

    Returns:
        Path to the generated ZIP archive.

    """
    cowsnphr_archive_folder = os.path.join(
        "olc_webportalv2", "media", "cowsnphr_archives"
    )
    os.makedirs(cowsnphr_archive_folder, exist_ok=True)
    archive_file = os.path.join(
        cowsnphr_archive_folder, f"{cowsnphr.project_name}_{cowsnphr.pk}"
    )
    try:
        os.remove(os.path.join(local_folder, "batch_config.txt"))
    except FileNotFoundError:
        pass
    shutil.make_archive(
        os.path.join(
            archive_file,
        ),
        "zip",
        local_folder,
    )
    return archive_file + ".zip"


def generate_archive_download_link(
    blob_client, container_name, output_zipfile, expiry=8
):
    """Upload an archive and create a time-limited download link.

    Args:
        blob_client: Provider-neutral storage client.
        container_name: Container receiving the archive.
        output_zipfile: Local path to the ZIP archive.
        expiry: Number of days the generated link remains valid.

    Returns:
        Download URL for the uploaded archive.

    """
    blob_name = os.path.basename(output_zipfile)
    blob_client.ensure_container(container_name)
    blob_client.upload_file(container_name, blob_name, output_zipfile)
    return blob_client.create_transfer_url(
        container_name,
        "download",
        name=blob_name,
        expires_in_seconds=expiry * 24 * 60 * 60,
    )
