from olc_webportalv2.cowbat.methods import AzureBatch
from sentry_sdk import capture_exception
from django.conf import settings
from celery import shared_task
import os

from .models import VirTyperAzureRequest, VirTyperFiles, VirTyperProject, VirTyperRequest


def make_config_file(job_name, sequences, input_data_folder, output_data_folder, command, config_file,
                     vm_size='Standard_D8s_v3'):
    """
    Makes a config file that can be submitted to AzureBatch via my super cool (and very poorly named)
    KubeJobSub package. Also, this assumes that you have settings imported so you have access to storage/batch names
    and keys
    :param sequences: List of sequences that are going to be analyzed.
    :param job_name: Name of the job to be run via Batch. Also, if a zip folder has to be created,
    it will be put in olc_webportalv2/media/job_name - this will get cleaned up if it exists by our monitor_tasks
    function
    :param input_data_folder: Name of folder on VM that FASTA sequences will be put into.
    :param output_data_folder: Name of folder on VM that output files will be written to.
    :param command: Command that's going to be run on the SeqIDs
    :param config_file: Where you want to save the config file to.
    :param vm_size: Size of VM you want to spin up. See
    https://docs.microsoft.com/en-us/azure/virtual-machines/linux/sizes-general for a list of options.
    :return:
    """
    with open(config_file, 'w') as f:
        f.write('BATCH_ACCOUNT_NAME:={}\n'.format(settings.BATCH_ACCOUNT_NAME))
        f.write('BATCH_ACCOUNT_KEY:={}\n'.format(settings.BATCH_ACCOUNT_KEY))
        f.write('BATCH_ACCOUNT_URL:={}\n'.format(settings.BATCH_ACCOUNT_URL))
        f.write('STORAGE_ACCOUNT_NAME:={}\n'.format(settings.AZURE_ACCOUNT_NAME))
        f.write('STORAGE_ACCOUNT_KEY:={}\n'.format(settings.AZURE_ACCOUNT_KEY))
        f.write('JOB_NAME:={}\n'.format(job_name))
        f.write('VM_IMAGE:={}\n'.format(settings.VM_IMAGE))
        f.write('VM_CLIENT_ID:={}\n'.format(settings.VM_CLIENT_ID))
        f.write('VM_SECRET:={}\n'.format(settings.VM_SECRET))
        f.write('VM_SIZE:={}\n'.format(vm_size))
        f.write('VM_TENANT:={}\n'.format(settings.VM_TENANT))
        # Desire format:
        # CLOUDIN:=container_name/file1 container_name/file_2 sequences
        f.write('CLOUDIN:=')
        for seq in sequences:
            f.write(os.path.join(job_name, '{seq} '.format(seq=seq)))
        f.write('{}\n'.format(input_data_folder))
        # Adding / to the end of output folder makes AzureBatch download recursively.
        if not output_data_folder.endswith('/'):
            output_data_folder += '/'
        f.write('OUTPUT:={}\n'.format(output_data_folder))
        f.write('COMMAND:={}\n'.format(command))


@shared_task
def run_vir_typer(vir_typer_request_pk):
    vir_typer_project = VirTyperProject.objects.get(pk=vir_typer_request_pk)
    vir_typer_samples = list(VirTyperRequest.objects.filter(project_name__pk=vir_typer_request_pk))
    try:
        container_name = VirTyperProject.objects.get(pk=vir_typer_request_pk).container_namer()
        run_folder = os.path.join('olc_webportalv2/media/{cn}'.format(cn=container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        sequences = list()
        for sample in vir_typer_samples:
            vir_files = list(VirTyperFiles.objects.filter(sample_name__pk=sample.pk))
            for vir_file in vir_files:
                sequences.append(vir_file.sequence_file)
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        command = 'source $CONDA/activate /envs/virustyper && mkdir {cn}'.format(cn=container_name)
        #
        command += ' && virustyper -r {container_name} -s sequences/'.format(container_name=container_name)
        make_config_file(job_name=container_name,
                         sequences=sequences,
                         input_data_folder='sequences',
                         output_data_folder=container_name,
                         command=command,
                         config_file=batch_config_file)
        # With that done, we can submit the file to batch with our package and create a tracking object.
        # subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
        #                 '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        azure_task = AzureBatch()
        azure_task.main(
            configuration_file='{run_folder}/batch_config.txt'.format(run_folder=run_folder),
            job_name=container_name,
            output_dir='olc_webportalv2/media',
            settings=settings,
            keep_input_container=True,
            download_output_files=False,
            no_clean=True,
        )
        VirTyperAzureRequest.objects.create(project_name=vir_typer_project,
                                            exit_code_file='NA')
        vir_typer_project.status = 'Processing'
        vir_typer_project.save()
    except Exception as e:
        capture_exception(e)
        vir_typer_project.status = 'Error'
        vir_typer_project.save()
