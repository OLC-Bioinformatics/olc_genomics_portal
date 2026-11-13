"""Celery tasks for submitting VirusTyper analyses."""

import os

from celery import shared_task
from sentry_sdk import capture_exception

from olc_webportalv2.common.methods import generic_api_submit

from .models import (
    VirTyperAzureRequest,
    VirTyperFiles,
    VirTyperProject,
    VirTyperRequest,
)


def make_config_file(
    job_name,
    sequences,
    input_data_folder,
    output_data_folder,
    command,
    config_file,
    vm_size="Standard_D2s_v3",
):
    """Write a provider-neutral job configuration file.

    Args:
        job_name: Name of the job and its input folder.
        sequences: Sequence filenames to include in the job.
        input_data_folder: VM folder containing the input sequences.
        output_data_folder: VM folder that receives output files.
        command: Command to run for the sequences.
        config_file: Local path where the configuration is written.
        vm_size: Requested virtual machine size for the Batch job.

    """
    with open(config_file, "w") as f:
        f.write(f"JOB_NAME:={job_name}\n")
        f.write(f"VM_SIZE:={vm_size}\n")
        # Desired format:
        # CLOUDIN:=container_name/file1 container_name/file_2 sequences
        f.write("CLOUDIN:=")
        for seq in sequences:
            f.write(os.path.join(job_name, f"{seq} "))
        f.write(f"{input_data_folder}\n")
        # A trailing slash makes Azure Batch download the output recursively.
        if not output_data_folder.endswith("/"):
            output_data_folder += "/"
        f.write(f"OUTPUT:={output_data_folder}\n")
        f.write(f"COMMAND:={command}\n")


@shared_task
def run_vir_typer(vir_typer_request_pk):
    """Submit a VirusTyper project to the internal Batch service.

    Args:
        vir_typer_request_pk: Primary key of the VirusTyper project.

    """
    vir_typer_project = VirTyperProject.objects.get(pk=vir_typer_request_pk)
    vir_typer_samples = list(
        VirTyperRequest.objects.filter(project_name__pk=vir_typer_request_pk)
    )
    try:
        container_name = vir_typer_project.container_namer()
        run_folder = os.path.join("olc_webportalv2/media", container_name)
        os.makedirs(run_folder, exist_ok=True)
        sequences = list()
        for sample in vir_typer_samples:
            vir_files = list(
                VirTyperFiles.objects.filter(sample_name__pk=sample.pk)
            )
            for vir_file in vir_files:
                sequences.append(vir_file.sequence_file)
        batch_config_file = os.path.join(run_folder, "batch_config.txt")
        command = (
            "source $CONDA/activate /envs/virustyper && "
            f"mkdir {container_name}"
        )
        command += f" && virustyper -r {container_name} -s sequences/"
        make_config_file(
            job_name=container_name,
            sequences=sequences,
            input_data_folder="sequences",
            output_data_folder=container_name,
            command=command,
            config_file=batch_config_file,
        )

        # Create the system call
        cmd = (
            "source $CONDA/activate /envs/virustyper && "
            f"virustyper -r $AZ_BATCH_NODE_MOUNTS_DIR/{container_name} "
            f"-s $AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/sequences/"
        )

        # Submit the command to the AzureBatch service
        generic_api_submit(
            command=cmd,
            container_name=container_name,
            vm_size="Standard_D2s_v3",
            unique_id="FoodPort",
        )
        VirTyperAzureRequest.objects.create(
            project_name=vir_typer_project, exit_code_file="NA"
        )
        vir_typer_project.status = "Processing"
        vir_typer_project.save()
    except Exception as e:
        capture_exception(e)
        vir_typer_project.status = "Error"
        vir_typer_project.save()
