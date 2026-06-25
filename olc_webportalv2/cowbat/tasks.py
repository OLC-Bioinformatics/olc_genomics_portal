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
from glob import glob
from io import StringIO
from time import sleep
from urllib.parse import quote
import fnmatch
import json
import logging
import os
import re
import shutil

# Azure-related imports
from azure.batch import BatchClient
from azure.batch.models import BatchTaskState
from azure.core.exceptions import (
    AzureError,
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.storage.blob import (
    BlobSasPermissions,
    generate_blob_sas,
)

# Django-related imports
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

# Third-party library imports
from Bio import SeqIO
from celery import shared_task
import ete3
import pandas as pd
from sentry_sdk import capture_exception
from strainchoosr import strainchoosr

# Local imports
from olc_webportalv2.ampliseq.models import AmpliSeqRequest
from olc_webportalv2.ampliseq.tasks import check_ampliseq_tasks
from olc_webportalv2.common.methods import (
    create_batch_client,
    create_blob_service,
    create_container,
    download_blob_to_path,
    download_container,
    generate_download_link,
    generic_api_submit,
    send_email,
    upload_blob_from_path,
)
from olc_webportalv2.cowbat.models import (
    SequencingRun,
    AzureTask,
    SummaryMetadata,
)
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


@shared_task
def run_cowbat_batch(
    sequencing_run_pk: int,
):
    """
    Run the cowbat batch task.

    Parameters:
    sequencing_run_pk (int): Primary key of the sequencing run.

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
        blob_service = create_blob_service()

        # Check if all files are present. If not, change status to
        # 'UploadError'
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        container_name = sequencing_run.run_name.lower().replace('_', '-')
        blob_filenames = []
        blobs = blob_service.get_container_client(container_name).list_blobs()
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
                blob_service_client=blob_service,
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
    except Exception as exc:
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        fh = logging.FileHandler(os.path.join(run_folder, 'error'))
        fh.setLevel(logging.INFO)
        logger.addHandler(fh)
        logger.exception(exc)
        capture_exception(exc)
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
    path = f'$AZ_BATCH_NODE_MOUNTS_DIR/{sequencing_run.container}'

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
        f'; pipeline_status=$?; '
        f'rsync -a /datadrive/{sequencing_run.container}/ '
        f'$AZ_BATCH_NODE_MOUNTS_DIR/{sequencing_run.container}/; '
        f'rsync_status=$?; '
        f'sync; '
        f'rm -rf /datadrive/{sequencing_run.container}; '
        f'if [ $pipeline_status -ne 0 ]; then exit $pipeline_status; fi; '
        f'if [ $rsync_status -ne 0 ]; then exit $rsync_status; fi; '
        f'exit 0'
    )


    # Submit the API call
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

    # Submit the API call and get the JSON data from the response
    response_data = generic_api_submit(
        command=command,
        container_name=sequencing_run.container,
        vm_size=vm_size,
        input_file_pattern=None,
        analysis_type="COWBAT",
        unique_id='FoodPort'
    )

    # Print the data
    print('Batch API response', response_data)

    # Update the model with the response data
    sequencing_run.pool_id = response_data.get('pool_id', '')
    sequencing_run.job_id = response_data.get('job_id', '')

    # This is only taking the first entry from the tasks, as there should
    # only be one
    tasks = response_data.get('tasks', [])
    sequencing_run.task_id = tasks[0] if tasks else ''
    sequencing_run.batch_submit_status = response_data.get('status', '')
    sequencing_run.batch_submit_errors = response_data.get('error', '')

    # Save the changes
    sequencing_run.save()

    # Create a new AzureTask object
    AzureTask.objects.create(
        sequencing_run=sequencing_run,
        exit_code_file=os.path.join(run_folder, 'exit_codes.txt')
    )


def nextseq_run(
    blob_service_client,
    blob_files: list,
    container_name: str,
    sequencing_run: SequencingRun,
) -> None:
    """
    Split NextSeq runs into manageable sizes, copy blobs to appropriate
    containers and run the assembly pipeline on each sub-sequencing run
    :param blob_service_client: BlobServiceClient object
    :param blob_files: List of blob files
    :param sequencing_run: SequencingRun object
    """
    # Check if a sample sheet is present
    if 'SampleSheet.csv' in blob_files:
        print('Sample sheet present')
        sub_runs = sample_sheet(
            blob_service_client=blob_service_client,
            container_name=container_name,
            run_name=sequencing_run.run_name,
            file_names=blob_files
        )
    else:
        # Create the sub_runs dictionary without a sample sheet
        sub_runs = no_sample_sheet(
            blob_service_client=blob_service_client,
            blob_files=blob_files,
            container_name=container_name,
        )

    # Store the base container name for use in creating sub-container names
    base_container_name = container_name
    
    # Submit the each sub-sequencing job to Azure Batch
    for i in sub_runs:
        run_name = sequencing_run.run_name
        sub_container_name = f'{base_container_name}-{i}'
        print(f'Processing run {run_name} in container {sub_container_name}')

        # Set the path to store the configuration file
        local_path = os.path.join(os.path.join(
            'olc_webportalv2',
            'media',
            run_name,
            f'sub_sample_sheets-{i}'
            )
        )

        # Create a SequenceRun object for each sub-sequencing run
        sub_sequencing_run = SequencingRun.objects.get_or_create(
            run_name=sub_container_name,
            container=sub_container_name,
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
        try:
            #  Recreate the archive name
            # archive_name = 'sub_sample_sheets-{i}.zip'.format(i=i)

            # Redefine the batch nodes path
            path = f'$AZ_BATCH_NODE_MOUNTS_DIR/{sub_container_name}'

            # Define the system call
            command = (
                f'source $CONDA/activate /envs/cowbat && '
                f'mkdir -p /datadrive/{sub_container_name} && '
                f'cp -R {path} /datadrive/ && '
                f'assembly_pipeline.py '
                f'-s /datadrive/{sub_container_name} '
                f'-r /databases/0.5.0.23 '
                f'; pipeline_status=$?; '
                f'rsync -a /datadrive/{sub_container_name}/ '
                f'$AZ_BATCH_NODE_MOUNTS_DIR/{sub_container_name}/; '
                f'rsync_status=$?; '
                f'sync; '
                f'rm -rf /datadrive/{sub_container_name}; '
                f'if [ $pipeline_status -ne 0 ]; then exit $pipeline_status; fi; '
                f'if [ $rsync_status -ne 0 ]; then exit $rsync_status; fi; '
                f'exit 0'
            )

            # Submit the API batch request
            cowbat_api_submit(
                command=command,
                run_folder=local_path,
                sequencing_run=sub_sequencing_run,
                vm_size='Standard_D48s_v3'
            )
        except Exception as exc:
            logger = logging.getLogger()
            logger.setLevel(logging.INFO)
            fh = logging.FileHandler(os.path.join(local_path, 'error'))
            fh.setLevel(logging.INFO)
            logger.addHandler(fh)
            logger.exception(exc)
            capture_exception(exc)
            SequencingRun.objects.filter(
                pk=sub_sequencing_run.pk
            ).update(status='Error')


def sample_sheet(
    blob_service_client,
    container_name: str,
    run_name: str,
    file_names: list,
    max_samples: int = 40
) -> dict:
    """
    Download the SampleSheet.csv from the blob container and parse it to
    determine the number of samples and the number of sub-runs required
    :param blob_service_client: BlobServiceClient object
    :param container_name: Name of the blob container
    :param run_name: Name of the sequencing run
    :param file_names: List of file names in the blob container
    :param max_samples (int): Maximum number of samples allowed in a sub-run.
    Default is 40
    :return: Dictionary containing the samples for each sub-run
    """
    # Download the SampleSheet.csv from the blob container
    download_blob_to_path(
        container_name=container_name,
        blob_name='SampleSheet.csv',
        file_path=os.path.join(
            'olc_webportalv2',
            'media',
            run_name,
            'SampleSheet.csv'
        ),
        blob_service_client=blob_service_client,
    )
    # Parse the SampleSheet.csv to determine the number of samples and the
    # number of sub-runs required
    sample_sheet_path = os.path.join(
        'olc_webportalv2',
        'media',
        run_name,
        'SampleSheet.csv'
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

    # Perform floor division to determine the number of sub-runs required
    # e.g. 192 samples / 40 samples = 4 sub-runs
    num_sub_runs = num_samples // max_samples

    # Calculate the remainder of the division to determine if there are any
    # samples left over that will be added to an additional sub-run
    # e.g. 192 samples % 40 samples = 32 samples, which does not equal zero,
    # so add an additional sub-run
    if num_samples % max_samples != 0:
        num_sub_runs += 1

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
        blob_service_client=blob_service_client,
        sub_runs=sub_runs_copy,
        container_name=container_name,
        header=header,
        file_path=os.path.join('olc_webportalv2', 'media', run_name),
        file_names=file_names
    )
    return sub_runs


def create_sub_sample_sheet(
        blob_service_client,
        container_name: str,
        file_names: list,
        file_path: str,
        header: list,
        sub_runs: dict):
    """
    Create a sub-sample sheet for each sub-run
    :param blob_service_client: BlobServiceClient object
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
        # Set the path to the sub-sample sheet
        sub_sample_sheet = header + sub_run
        sub_sample_sheet_path = sample_sheet_path + f'-{i}'

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
        sub_container_name = create_sub_container(
            blob_service_client=blob_service_client,
            container_name=container_name,
            i=i,
        )

        # Iterate over the samples in the sub-run and copy the FASTQ files to
        # the sub-container
        for sample in sorted(sub_run):
            # Extract the sample name from the sample line
            sample_name = sample[0]

            # Copy the FASTQ files for the sample to the sub-container
            copy_blobs(
                blob_service_client=blob_service_client,
                container_name=container_name,
                sample_name=sample_name,
                file_names=file_names,
                sub_container_name=sub_container_name,
            )

        # Upload the sample sheet to the sub-container
        upload_blob_from_path(
            container_name=sub_container_name,
            blob_name='SampleSheet.csv',
            file_path=os.path.join(
                sub_sample_sheet_path,
                'SampleSheet.csv'
            ),
            blob_service_client=blob_service_client,
        )


def create_sub_container(
    blob_service_client,
    container_name: str,
    i: int
):
    """
    Create a sub-container for the sub-run
    :param blob_service_client: BlobServiceClient object
    :param container_name: Name of the blob container
    :param i: Index of the sub-run
    :return: Name of the sub-container
    """
    sub_container_name = container_name + f'-{i}'
    create_container(
        container_name=sub_container_name,
        blob_service_client=blob_service_client,
    )
    return sub_container_name


def copy_blobs(
    blob_service_client,
    container_name: str,
    file_names: list,
    sample_name: str,
    sub_container_name: str,
):
    """
    Copy blobs from a container to a sub-container
    :param blob_service_client: BlobServiceClient object
    :param container_name: Name of the blob container
    :param file_names: List of file names in the blob container
    :param sample_name: Name of the sample
    :param sub_container_name: Name of the sub-container
    """
    fastq_files = [fastq for fastq in file_names if sample_name in fastq]

    for fastq in fastq_files:
        sas_token = generate_blob_sas(
            account_name=settings.AZURE_ACCOUNT_NAME,
            container_name=container_name,
            blob_name=fastq,
            account_key=settings.AZURE_ACCOUNT_KEY,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        
        source_url = (
            f"{blob_service_client.url}/"
            f"{container_name}/"
            f"{quote(fastq, safe='/')}?"
            f"{sas_token}"
        )

        dest_blob_client = blob_service_client.get_blob_client(
            container=sub_container_name,
            blob=fastq,
        )
        dest_blob_client.start_copy_from_url(source_url)


def archive_sub_run(
    blob_service_client,
    local_path: str,
    sub_container_name: str
):
    """
    Create an archive of the FASTQ files (and sample sheet if present) for the
    sub-run and upload it to the destination
    """
    shutil.make_archive(local_path, 'zip', local_path)
    archive = local_path + '.zip'
    blob_file = os.path.basename(archive)

    upload_blob_from_path(
        container_name=sub_container_name,
        blob_name=blob_file,
        file_path=archive,
        blob_service_client=blob_service_client,
    )

    os.remove(archive)
    fastq_files = glob(os.path.join(local_path, '*.fastq.gz'))
    for fastq in fastq_files:
        os.remove(fastq)


def no_sample_sheet(
    blob_service_client,
    blob_files: list,
    container_name: str,
    max_samples: int = 40
) -> dict:
    """
    Count the number of samples in a blob container and distribute the
    samples across sub-runs
    :param blob_service_client: BlobServiceClient object
    :param blob_files: List of file names in the blob container
    :param container_name: Name of the blob container
    :param max_samples: Maximum number of samples per sub-run
    :return: Dictionary containing the samples for each sub-run
    """
    sample_names = {
        fastq.split('_')[0] for fastq in blob_files if fastq.endswith('.gz')
        }

    num_samples = len(sample_names)
    num_sub_runs = num_samples // max_samples
    if num_samples % max_samples != 0:
        num_sub_runs += 1

    sub_runs = {i + 1: [] for i in range(num_sub_runs)}

    for i, sample in enumerate(sorted(sample_names)):
        sub_run_index = i // max_samples
        sub_runs[sub_run_index + 1].append(sample)

    for i, sub_run in sub_runs.items():
        sub_container_name = create_sub_container(
            blob_service_client=blob_service_client,
            container_name=container_name,
            i=i,
        )

        for sample_name in sorted(sub_run):
            copy_blobs(
                blob_service_client=blob_service_client,
                container_name=container_name,
                sample_name=sample_name,
                file_names=blob_files,
                sub_container_name=sub_container_name,
            )

    return sub_runs


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
    # Get the SequencingRun object
    sequencing_run = SequencingRun.objects.get(pk=sequencing_run_pk)

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
    blob_service = create_blob_service()
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

    # List all the things in the container - if it's a file in reports folder
    # or an assembly, download it.
    blobs = list(blob_service.get_container_client(container_name).list_blobs())
    blob_filenames = [b.name for b in blobs]
    for blob in blobs:
        if fnmatch.fnmatch(
            blob.name,
            os.path.join("BestAssemblies", "*.fasta")
        ):
            download_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    assemblies_folder,
                    os.path.split(blob.name)[1]
                ),
                blob_service_client=blob_service,
            )
        elif fnmatch.fnmatch(blob.name, os.path.join("reports", "*.csv")):
            download_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    reports_folder,
                    os.path.split(blob.name)[1]
                ),
                blob_service_client=blob_service,
            )
        elif fnmatch.fnmatch(blob.name, os.path.join("reports", "*.tsv")):
            download_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    reports_folder,
                    os.path.split(blob.name)[1]
                ),
                blob_service_client=blob_service,
            )
        elif fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'reports',
                '*.fa'
            )
        ):
            download_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    reports_folder,
                    os.path.split(blob.name)[1]
                ),
                blob_service_client=blob_service,
            )
        elif fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'reports',
                '*.xlsx'
            )
        ):
            download_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    reports_folder,
                    os.path.split(blob.name)[1]
                ),
                blob_service_client=blob_service,
            )

        # Also get the SampleSheet put into the reports folder.
        elif fnmatch.fnmatch(
            blob.name,
            os.path.join(
                'SampleSheet.csv'
            )
        ):
            download_blob_to_path(
                container_name=container_name,
                blob_name=blob.name,
                file_path=os.path.join(
                    reports_folder,
                    os.path.split(blob.name)[1]
                ),
                blob_service_client=blob_service,
            )

    # Update combinedMetadata.csv with read‑filenames
    add_read_filenames_to_metadata(
        sequencing_run=sequencing_run,
        blob_service_client=blob_service,
        container_name=container_name,
        reports_folder=reports_folder,
        blob_filenames=blob_filenames,
    )

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

    # Set the name of the blob container where the zip file will be uploaded
    report_assembly_container = 'reports-and-assemblies'

    # Upload the zip file to the blob container
    upload_blob_from_path(
        container_name=report_assembly_container,
        blob_name=blob_name,
        file_path=os.path.join(
            run_folder,
            blob_name
        ),
        blob_service_client=blob_service,
    )

    # Generate a SAS URL for the zip file and update the SequencingRun object
    # with the download link
    sas_url = generate_download_link(
        blob_service_client=blob_service,
        container_name=report_assembly_container,
        blob_name=os.path.basename(os.path.join(
            run_folder,
            blob_name
        )),
        expiry=730
    )

    # Update the SequencingRun object with the download link and remove the
    # local media folder for the run to save space.
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

    # Create a list of recipients based on the environment. In production,
    # send to default recipients
    if settings.ENVIRONMENT == 'PROD':
        recipient_list = [
            'catherine.carrillo@inspection.gc.ca',
            'monique.arts@inspection.gc.ca',
            'adam.koziol@inspection.gc.ca',
            'ashley.cooper@inspection.gc.ca',
            'bridgette.kelly@inspection.gc.ca'
        ]

        # Send customized emails to each recipient based on their role
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
                f'following link: {sas_url}\n'
            )
            if realtime_strains:
                body += (
                    'In this run, the following strains will need '
                    f'ROGAs created: {realtime_strains}'

                )

            # Attempt to send the emails
            send_email(
                subject=f'Run {sequencing_run} has finished assembly.',
                body=body,
                recipient=recipient
            )


def add_read_filenames_to_metadata(
    sequencing_run: SequencingRun,
    blob_service_client,
    container_name: str,
    reports_folder: str,
    blob_filenames: list,
) -> None:
    """
    Add two columns, ``R1_file`` and ``R2_file`` to
    ``combinedMetadata.csv`` and upload the modified file to the run container.

    ``blob_filenames`` should be a simple list of all blob names in the
    container (typically the result of ``[b.name for b in blobs]``).

    The columns are only created once; if they already exist we simply update
    empty cells and leave existing data alone.  Any seqid for which the
    forward/reverse lists are empty is recorded in
    ``sequencing_run.errors``.
    """
    # Set the path to the combinedMetadata.csv file
    csv_name = "combinedMetadata.csv"
    csv_path = os.path.join(reports_folder, csv_name)

    # Check if the combinedMetadata.csv file exists; if not, return early
    if not os.path.isfile(csv_path):
        # nothing to do if the report is not present
        return

    # Read the CSV into a DataFrame
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:  # pragma: no cover
        sequencing_run.errors.append(
            f"could not read {csv_name}: {exc}"
        )
        sequencing_run.save()
        return

    # Convert any existing columns to string/object and replace NaN with ''
    for col in ('R1_file', 'R2_file'):
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str)

    # Populate
    for idx, row in df.iterrows():
        seqid = str(row.get("SeqID", "")).strip()

        # Find matching forward and reverse reads
        forwards = fnmatch.filter(blob_filenames, seqid + "*_R1*.fastq.gz")
        # Remove trimmed, repaired, corrected, or otherwise modified reads
        # from consideration
        forwards = [
            f for f in forwards if not any(
                x in f for x in ["trimmed", "repaired", "corrected"]
            )
        ]

        # Find matching reverse reads
        reverses = fnmatch.filter(blob_filenames, seqid + "*_R2*.fastq.gz")
        # Remove trimmed, repaired, corrected, or otherwise modified reads
        # from consideration
        reverses = [
            f for f in reverses if not any(
                x in f for x in ["trimmed", "repaired", "corrected"]
            )
        ]

        # Only write into blank cells (after the fillna() above a blank cell is
        # exactly the empty string)
        if df.at[idx, 'R1_file'] == '':
            if len(forwards) == 1:
                df.at[idx, 'R1_file'] = forwards[0]
            elif len(forwards) > 1:
                df.at[idx, 'R1_file'] = ';'.join(forwards)
        if df.at[idx, 'R2_file'] == '':
            if len(reverses) == 1:
                df.at[idx, 'R2_file'] = reverses[0]
            elif len(reverses) > 1:
                df.at[idx, 'R2_file'] = ';'.join(reverses)

        if len(forwards) != 1 or len(reverses) != 1:
            sequencing_run.errors.append(
                f"read‑pair problem for {seqid}: forwards={forwards}, "
                f"reverses={reverses}"
            )

    # Write back and upload
    try:
        df.to_csv(csv_path, index=False)
        upload_blob_from_path(
            container_name=container_name,
            blob_name=os.path.join("reports", csv_name),
            file_path=csv_path,
            blob_service_client=blob_service_client,
        )

    except Exception as exc:  # pragma: no cover
        sequencing_run.errors.append(
            f"error writing/uploading {csv_name}: {exc}"
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
    # Ensure that the model status is set to "Processing"
    sequencing_run.status = 'Processing'
    sequencing_run.save()
    node_files = batch_client.file.list_from_task(
        job_id=batch_job_name,
        task_id=batch_task_name,
        recursive=True
    )

    # Initialize dictionaries to hold the contents of the files and the
    # text files
    contents = {}
    text_files = {}

    # Create an output directory for the files
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
                    sequencing_run.errors.append(str(exc))
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
                    sequencing_run.errors.append(str(exc))
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
                    sequencing_run.errors.append(str(exc))
                    sequencing_run.save()
    except (AzureError, HttpResponseError) as exc:
        sequencing_run.errors.append('Azure error:')
        sequencing_run.errors.append(str(exc))
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
                sequencing_run.errors.append(str(exc))
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
        sequencing_run.errors.append(str(exc))
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
    batch_client = create_batch_client()

    for azure_task in azure_tasks:
        sequencing_run = SequencingRun.objects.get(
            pk=azure_task.sequencing_run.pk
        )
        batch_job_name = sequencing_run.job_id

        # Check if all tasks associated with this job have completed
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != BatchTaskState.COMPLETED:
                    tasks_completed = False
        except (AzureError, HttpResponseError) as exc:
            sequencing_run.errors.append('Running task error:')
            sequencing_run.errors.append(str(exc))
            sequencing_run.save()
            continue

        # If tasks have completed, check exit codes
        if tasks_completed:
            # Handle specific error case
            if sequencing_run.status == 'Resize Error':
                handle_resize_error(
                    sequencing_run=sequencing_run,
                    batch_job_name=batch_job_name,
                    azure_task_id=azure_task.id
                )

            # Tasks are complete for the Azure task
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
            f'sub_sample_sheets-{digits}'
        )
    else:
        # Create a configuration file to be used by the Azure batch script.
        local_folder = os.path.join(
            'olc_webportalv2',
            'media',
            str(sequencing_run)
        )

    # Resubmit the batch request
    submit_batch(
        run_folder=local_folder,
        sequencing_run=sequencing_run
    )

    # Delete task, so we don't have to keep checking up on it.
    AzureTask.objects.filter(id=azure_task_id).delete()


def handle_task_completion(
    batch_client,
    sequencing_run,
    batch_job_name,
    azure_task_id
):
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
                sequencing_run.errors.append(str(cloudtask.execution_info))
    except (AzureError, HttpResponseError) as exc:
        sequencing_run.errors.append('Terminating task error:')
        sequencing_run.errors.append(str(exc))
        sequencing_run.save()
        return

    # Get rid of job and pool, so we don't waste big $$$ and do cleanup/get
    # files downloaded in tasks.
    try:
        batch_client.job.delete(job_id=batch_job_name)
    except (AzureError, HttpResponseError) as exc:
        sequencing_run.errors.append('Terminating job error:')
        sequencing_run.errors.append(str(exc))
        sequencing_run.save()
    try:
        batch_client.pool.delete(pool_id=sequencing_run.pool_id)
    except (AzureError, HttpResponseError) as exc:
        sequencing_run.errors.append('Terminating pool error:')
        sequencing_run.errors.append(str(exc))
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
            sequencing_run.errors.append(str(exc))
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
        sequencing_run.errors.append(str(exc))
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
    try:
        pools = list(batch_client.pool.list())
    except AzureError as exc:
        sequencing_run.errors.append('Pool listing error:')
        sequencing_run.errors.append(str(exc))
        sequencing_run.save()
        return

    # Determine whether the batch job is in the list of pools
    present = batch_job_name in [pool.id for pool in pools]
    if not present and sequencing_run.status == 'Resize Error':
        handle_resize_error(sequencing_run, batch_job_name, azure_task_id)

    # Recreate the pools generator
    try:
        pools = list(batch_client.pool.list())
    except AzureError as exc:
        sequencing_run.errors.append('Pool listing error:')
        sequencing_run.errors.append(str(exc))
        sequencing_run.save()
        return

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
    batch_client = create_batch_client()

    # Iterate over each tree task
    for tree_task in tree_tasks:
        # Fetch the corresponding tree object
        tree_object = Tree.objects.get(
            pk=tree_task.tree_request.pk
        )

        # Construct the batch job name
        batch_job_name = f'tree-{tree_task.tree_request.pk}'

        try:
            # Check if all tasks related to this job have completed
            tasks_completed = all(
                task.state == BatchTaskState.COMPLETED
                for task in batch_client.task.list(batch_job_name)
            )
        except (AzureError, HttpResponseError) as exc:
            # If job doesn't exist, update status to 'Error' and delete
            # the task
            Tree.objects.filter(
                pk=tree_task.tree_request.pk
            ).update(status='Error')
            TreeAzureRequest.objects.filter(
                id=tree_task.id
            ).delete()
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
                blob_client = create_blob_service()

                download_container(
                    blob_service_client=blob_client,
                    container_name=batch_job_name,
                    output_dir=os.path.join(
                        'olc_webportalv2',
                        'media'
                    )
                )

                # Open the tree file and read the first line
                tree_file = os.path.join(
                    'olc_webportalv2', 'media',
                    f'tree-{tree_object.pk}', 'mash.tree'
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
                for suffix in ['-input', '-output']:
                    try:
                        blob_client.delete_container(
                            container_name=f'tree-{tree_object.pk}{suffix}'
                        )
                    except ResourceNotFoundError:
                        pass

                # Prepare the output folder and remove the batch config file
                tree_output_folder = os.path.join(
                    'olc_webportalv2', 'media',
                    f'tree-{tree_object.pk}'
                )
                os.remove(
                    os.path.join(tree_output_folder, 'batch_config.txt')
                )

                # Create a variable to store the zip path
                zip_path = f'{tree_output_folder}.zip'

                # Zip the output folder and upload it to the cloud
                shutil.make_archive(
                    tree_output_folder,
                    'zip',
                    tree_output_folder
                )
                tree_result_container = f'tree-{tree_object.pk}'

                # Upload the zip file to the cloud and generate a SAS URL for download
                upload_blob_from_path(
                    blob_service_client=blob_client,
                    container_name=tree_result_container,
                    blob_name=os.path.basename(zip_path),
                    file_path=zip_path
                )
                sas_url = generate_download_link(
                    blob_service_client=blob_client,
                    container_name=tree_result_container,
                    blob_name=os.path.basename(zip_path),
                    expiry=8
                )

                # Remove the output folder and the zip file
                shutil.rmtree(tree_output_folder)
                zip_folder = os.path.join(
                    'olc_webportalv2', 'media',
                    f'{batch_job_name}.zip'
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
    batch_client = create_batch_client()

    # Iterate over each AMR summary task
    for amr_task in amr_summary_tasks:
        # Fetch the corresponding AMR summary object
        amr_object = AMRSummary.objects.get(
            pk=amr_task.amr_request.pk
        )

        # Construct the batch job name
        batch_job_name = f'amrsummary-{amr_task.amr_request.pk}'

        # Assume all tasks related to this job have completed
        tasks_completed = True

        try:
            # Check if all tasks related to this job have completed
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != BatchTaskState.COMPLETED:
                    tasks_completed = False
        # If job doesn't exist, update status to 'Error' and delete the task
        except (AzureError, HttpResponseError):
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
                blob_client = create_blob_service()

                download_container(
                    blob_service_client=blob_client,
                    container_name=batch_job_name,
                    output_dir='olc_webportalv2/media'
                )

                output_dir = f'olc_webportalv2/media/{batch_job_name}'
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

                amr_result_container = f'amrsummary-{amr_object.pk}'

                # Generate a SAS URL for the zip file and update the
                # AMRSummary object with the download link
                upload_blob_from_path(
                    blob_service_client=blob_client,
                    container_name=amr_result_container,
                    blob_name=os.path.basename(f'{output_dir}.zip'),
                    file_path=f'{output_dir}.zip'
                )

                sas_url = generate_download_link(
                    blob_service_client=blob_client,
                    container_name=amr_result_container,
                    blob_name=os.path.basename(f'{output_dir}.zip'),
                    expiry=8
                )

                # Populate the AMRDetail model with results
                seq_amr_dict = {}
                for seqid in amr_object.seqids:
                    seq_amr_dict[seqid] = {}
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
                            seq_amr_dict[seqid] = {}

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
    batch_client = create_batch_client()

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
                if cloudtask.state != BatchTaskState.COMPLETED:
                    tasks_completed = False
        # Catch specific Azure Batch exceptions
        except (AzureError, HttpResponseError):
            VirTyperProject.objects.filter(
                pk=vir_typer_task.pk).update(status='Error')
            VirTyperAzureRequest.objects.filter(id=sub_task.id).delete()
            continue
        except Exception:  # Catch all other exceptions
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
                blob_client = create_blob_service()

                vir_typer_result_container = batch_job_name

                download_container(
                    blob_service_client=blob_client,
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

                # Upload the zip file to the cloud and generate a SAS URL for
                # download
                upload_blob_from_path(
                    blob_service_client=blob_client,
                    container_name=vir_typer_result_container,
                    blob_name=os.path.basename(output_dir + '.zip'),
                    file_path=output_dir + '.zip'
                )
                sas_url = generate_download_link(
                    blob_service_client=blob_client,
                    container_name=vir_typer_result_container,
                    blob_name=os.path.basename(output_dir + '.zip'),
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
    batch_client = create_batch_client()

    # Iterate over each Prokka task
    for prokka_task in prokka_tasks:
        # Fetch the corresponding Prokka object
        prokka_object = ProkkaRequest.objects.get(
            pk=prokka_task.prokka_request.pk
        )

        # Create a job name for the batch
        batch_job_name = f'prokka-{prokka_task.prokka_request.pk}'

        # Assume all tasks are completed
        tasks_completed = True

        try:
            # Check the status of each task in the batch
            for cloud_task in batch_client.task.list(batch_job_name):
                # If any task is not completed, set tasks_completed to False
                if cloud_task.state != BatchTaskState.COMPLETED:
                    tasks_completed = False
        except (AzureError, HttpResponseError):
            # Handle exceptions
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
    batch_client: BatchClient,
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
    blob_client = create_blob_service()

    download_container(
        blob_service_client=blob_client,
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
    prokka_result_container = f'prokka-result-{prokka_object.pk}'

    # Upload the zip file to the cloud
    upload_blob_from_path(
        blob_service_client=blob_client,
        container_name=prokka_result_container,
        blob_name=os.path.basename(output_dir + '.zip'),
        file_path=output_dir + '.zip'
    )

    # Generate a SAS URL for the zip file
    sas_url = generate_download_link(
        blob_service_client=blob_client,
        container_name=prokka_result_container,
        blob_name=os.path.basename(output_dir + '.zip'),
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
        GeneSeekrDetail.objects.filter(seqid=seqid).delete()
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
                f'{seqid}*.tsv')
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
            modified_contig_name = f'{seqid}_{blast_result.query_id}'

            # Remove any previously populated versions
            TopBlastHit.objects.filter(
                contig_name=modified_contig_name
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
                gene_name=blast_result.subject_id.replace(
                    'gb|', ''
                ).replace(
                    '|', ''
                ),
                query_start_position=blast_result.subject_start,
                query_end_position=blast_result.subject_end,
                query_sequence_length=blast_result.subject_length
            )

            # Save the hit
            top_blast_hit.save()


def check_geneseekr_tasks():
    """
    This function checks the status of GeneSeekr tasks and updates their
    status. It also handles errors, cleans up completed tasks, and sends
    emails to users.
    """

    # Get all GeneSeekr tasks
    geneseekr_tasks = GeneSeekrAzureRequest.objects.filter()

    # Create credentials for the batch client
    batch_client = create_batch_client()

    # Iterate over each GeneSeekr task
    for geneseekr_task in geneseekr_tasks:

        # Get the corresponding GeneSeekr request
        geneseekr_request = GeneSeekrRequest.objects.get(
            pk=geneseekr_task.geneseekr_request.pk
        )

        # Create a name for the batch job
        batch_job_name = f'geneseekr-{geneseekr_task.geneseekr_request.pk}'

        # Initialize a flag to check if all tasks have completed
        tasks_completed = True

        # Try to list all tasks for the batch job
        try:
            for cloudtask in batch_client.task.list(batch_job_name):

                # If any task is not completed, set the flag to False
                if cloudtask.state != BatchTaskState.COMPLETED:
                    tasks_completed = False

        # If an Azure-related error occurs, handle it
        except (AzureError, HttpResponseError):
            GeneSeekrRequest.objects.filter(
                pk=geneseekr_task.geneseekr_request.pk
            ).update(status='Error')
            GeneSeekrAzureRequest.objects.filter(id=geneseekr_task.id).delete()
            continue

        # If a general exception occurs, handle it
        except Exception:
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

                blob_client = create_blob_service()

                blobs = blob_client.get_container_client(output_container).list_blobs()

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
                        download_blob_to_path(
                            container_name=output_container,
                            blob_name=blob.name,
                            file_path=os.path.join(
                                run_folder,
                                os.path.split(blob.name)[1]
                            ),
                            blob_service_client=blob_client,
                        )
                    elif fnmatch.fnmatch(
                        blob.name,
                        os.path.join(
                            'reports',
                            '*.tsv'
                        )
                    ):
                        download_blob_to_path(
                            container_name=output_container,
                            blob_name=blob.name,
                            file_path=os.path.join(
                                run_folder,
                                os.path.split(blob.name)[1]
                            ),
                            blob_service_client=blob_client,
                        )

                # If the GeneSeekr request is a benchmark, update the seqids
                if geneseekr_request.benchmark:
                    geneseekr_request.seqids = []
                    geneseekr_request.save()
                    benchmark_file = os.path.join(
                        'olc_webportalv2',
                        'geneseekr',
                        geneseekr_request.benchmark.lower() +
                        '_benchmark_ids.txt'
                    )
                    with open(
                            benchmark_file,
                            'r',
                            encoding='utf-8'
                            ) as seq_ids:
                        for line in seq_ids:
                            geneseekr_request.seqids.append(line.rstrip())
                    geneseekr_request.save()

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
                sas_url = generate_download_link(
                    blob_service_client=blob_client,
                    container_name=output_container,
                    blob_name='reports/geneseekr_blastn.xlsx',
                    expiry=8,
                )
                sas_url_sequence = generate_download_link(
                    blob_service_client=blob_client,
                    container_name=output_container,
                    blob_name='reports/geneseekr_blastn.csv',
                    expiry=8,
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
                        subject=f'Geneseekr Query {geneseekr_request} has '
                        'finished.',
                        body=f'This email is to inform you that the Geneseekr'
                        f' Query {geneseekr_request} has completed and is '
                        f'available at the following link {sas_url}',
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
    adapter = (
        f"{dataframes['BCLConvert_Settings'].get('AdapterRead1', '')}_"
        f"{dataframes['BCLConvert_Settings'].get('AdapterRead2', '')}"
    )

    # Create the MiSeq sample sheet
    with open(output_file_path, 'w', encoding='utf-8') as file:
        file.write(f'{header_section}\n')
        file.write(f'Experiment Name,{experiment_name}\n')
        file.write(f'\n{reads_section}\n')
        file.write(f'{forward_read_length}\n')
        file.write(f'{reverse_read_length}\n')
        file.write(f'\n{settings_section}\n')
        file.write(f'adapter,{adapter}\n')
        file.write(f'\n{data_section}\n')
        file.write(','.join(data_columns) + '\n')
        for seq_id, seq_dict in sorted(dataframes['Cloud_Data'].items()):
            description = ''
            i7_index = dataframes['BCLConvert_Data'][seq_id].get('Index')
            i5_index = dataframes['BCLConvert_Data'][seq_id].get('Index2')
            sample_project = ''
            sample_plate = seq_dict.get('ProjectName', '')
            sample_well = ''
            file.write(
                f'{seq_id},{seq_id},{description},{i7_index},{i7_index},'
                f'{i5_index},{i5_index},{sample_project},{sample_plate},'
                f'{sample_well}\n'
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
            f'The file {file_path} does not exist.'
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
    blob_client = create_blob_service()
    # Patterns we have to worry about - data-request-digits, geneseekr-digits
    # TODO: Add more of these as more analysis types get created.
    patterns_to_search = [
        re.compile('^ampliseq.+'),
        re.compile(r'^amrsummary-\d+-\w+$'),
        re.compile('^cowsnphr.+'),
        re.compile(r'^data-request-\d+$'),
        re.compile(r'^geneseekr-\d+-\w+$'),
        re.compile(r'^mash-\d+-\w+$'),
        re.compile(r'^neighbor-\d+$'),
        re.compile(r'^neighbor-\w+-\d+$'),
        re.compile(r'^parsnp-\d+-\w+$'),
        re.compile(r'^primer-\w+-\d+$'),
        re.compile(r'^primer-\w+-\d+-\w+$'),
        re.compile(r'^prokka-\d+-\w+$'),
        re.compile(r'^tree-\d+-\w+$'),
        re.compile(r'^vir-typer-\d+-\w+$')]
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
