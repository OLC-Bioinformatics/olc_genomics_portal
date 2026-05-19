#!/usr/bin/env python

# Standard imports
from glob import glob
import shutil
import os

# Third-party imports
from email.mime.multipart import MIMEMultipart
from sentry_sdk import capture_exception
from email.mime.text import MIMEText
from celery import shared_task
import smtplib

# Django imports
from django.conf import settings

# Azure imports
from azure.storage.blob import BlockBlobService

# Local imports
from olc_webportalv2.common.methods import generic_api_submit
from olc_webportalv2.geneseekr.methods import zip_files
from olc_webportalv2.geneseekr.models import GeneSeekrAzureRequest, \
    GeneSeekrRequest, \
    Tree, \
    TreeAzureRequest, \
    AMRSummary, \
    AMRAzureRequest, \
    ProkkaRequest, \
    ProkkaAzureRequest, \
    NearestNeighbors, \
    NearNeighborDetail
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.cowbat.tasks import generate_download_link


def make_config_file(seqids, job_name, input_data_folder, output_data_folder, command, config_file,
                     vm_size='Standard_D8s_v3', other_input_files=None, target=None, benchmark=None):
    """
    Makes a config file that can be submitted to AzureBatch via my super cool (and very poorly named)
    KubeJobSub package. Also, this assumes that you have settings imported so you have access to storage/batch names and keys
    :param seqids: List of SeqIDs that are going to be analyzed.
    :param job_name: Name of the job to be run via Batch. Also, if a zip folder has to be created,
    it will be put in olc_webportalv2/media/job_name - this will get cleaned up if it exists by our monitor_tasks function
    :param input_data_folder: Name of folder on VM that FASTA sequences will be put into.
    :param output_data_folder: Name of folder on VM that output files will be written to.
    :param command: Command that's going to be run on the SeqIDs
    :param config_file: Where you want to save the config file to.
    :param vm_size: Size of VM you want to spin up.
    See https://docs.microsoft.com/en-us/azure/virtual-machines/linux/sizes-general
    for a list of options.
    :param other_input_files: List of other files to put into input folder. Each entry in list should be a string
    in format container_name/file_name
    :return:
    """
    # Azure Batch does not like it one bit when too many input files get specified, so in the event that we have too
    # many (more than 50 or so), we need to download them, zip them, and then upload the zip folder.
    if other_input_files is None:
        other_input_files = list()
    output_container_name = '{job_name}-input'.format(job_name=job_name)
    if len(seqids) > 50:
        blob_file = zip_files(
            seqids=seqids,
            target_folder=job_name,
            container_name='processed-data'
        )
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
        if len(seqids) > 50:
            f.write('CLOUDIN:={archive}\n'.format(
                archive=os.path.join(
                    'temporary-storage',
                    blob_file
                )
            ))
            # If we have to add lots of files, prepend that to our command.
            prepend = 'unzip {zipfile} && mkdir -p {input_dir} && mv *.fasta {input_dir} && ' \
                'rm {zipfile} && '.format(
                zipfile=job_name + '.zip',
                input_dir=input_data_folder
            )
            command = prepend + command
        else:
            if len(seqids) > 0:
                f.write('CLOUDIN:=')
                for seqid in seqids:
                    f.write('processed-data/{}.fasta '.format(seqid))
                f.write('{}\n'.format(input_data_folder))
        if benchmark:
            benchmark_lookup = {
                'Listeria': 'listeria_benchmark.zip',
                'VTEC': 'vtec_benchmark.zip'
            }
            blob_file = benchmark_lookup[benchmark]
            f.write('CLOUDIN:={archive}\n'.format(
                archive=os.path.join(
                    'benchmarks',
                    blob_file
                )
            ))
            # Prepend the unzipping of the benchmark dataset archive, creation of the sequences
            # folder, and moving the FASTA files to the sequence folder to the command
            # prepend = 'unzip {zipfile} && mkdir -p {input_dir} && mv *.fasta {input_dir} && ' \
            #     'rm {zipfile} && '.format(
            #     zipfile=blob_file,
            #     input_dir=input_data_folder
            # )
            # command = prepend + command
        if len(other_input_files) > 0:
            f.write('CLOUDIN:=')
            for other_file in other_input_files:
                f.write('{} '.format(other_file))
            f.write('{}\n'.format(input_data_folder))
        if target:
            f.write('CLOUDIN:={target_container}/query.fasta targets\n'.format(target_container=output_container_name))
        # Adding / to the end of output folder makes AzureBatch download recursively.
        if not output_data_folder.endswith('/'):
            output_data_folder += '/'
        f.write('OUTPUT:={}\n'.format(output_data_folder))
        f.write('COMMAND:={}\n'.format(command))


@shared_task
def run_prokka(prokka_request_pk):
    prokka_request = ProkkaRequest.objects.get(pk=prokka_request_pk)
    try:
        container_name = 'prokka-{}'.format(prokka_request_pk)
        run_folder = os.path.join('olc_webportalv2/media/{}'.format(container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        # batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # command = 'source $CONDA/activate /envs/prokka && mkdir {}'.format(container_name)
        # # Prokka doesn't seem to have any sort of way to run on multiple genomes, so we have to run it separately
        # # on each genome.
        # # TODO: Make sure this still works with a really long command caused by lots of SEQIDs
        # for seqid in prokka_request.seqids:
        #     command += ' && prokka --outdir {container_name}/{seqid} --prefix {seqid} ' \
        #                '--cpus 8 sequences/{seqid}.fasta'\
        #         .format(
        #             container_name=container_name,
        #             seqid=seqid
        #         )
        # for other_file in prokka_request.other_input_files:
        #     command += ' && prokka --outdir {} --prefix {} --cpus 8 sequences/{}'\
        #         .format(
        #             other_file,
        #             os.path.split(other_file)[1].replace('.fasta', ''),
        #             os.path.split(other_file)[1]
        #         )
        # make_config_file(
        #     seqids=prokka_request.seqids,
        #     job_name=container_name,
        #     input_data_folder='sequences',
        #     output_data_folder=container_name,
        #     command=command,
        #     config_file=batch_config_file,
        #     other_input_files=prokka_request.other_input_files
        # )
        # With that done, we can submit the file to batch with our package and create a tracking object.
        # subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
        #                 '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        # azure_task = AzureBatch()
        # azure_task.main(
        #     configuration_file='{run_folder}/batch_config.txt'.format(run_folder=run_folder),
        #     job_name=container_name,
        #     output_dir='olc_webportalv2/media',
        #     settings=settings,
        #     keep_input_container=True,
        #     download_output_files=False,
        #     vm_size='Standard_D8s_v3',
        #     no_clean=True,
        # )

        # Create the command for the batch request
        cmd = 'source $CONDA/activate /envs/prokka && '

        for seqid in prokka_request.seqids:
            cmd += ' && prokka ' \
                '--outdir $AZ_BATCH_NODE_MOUNTS_DIR/{container}/{seqid} ' \
                '--prefix {seqid} ' \
                '--cpus 8 sequences/{seqid}.fasta'\
                .format(
                    container=container_name,
                    seqid=seqid
                )
        for other_file in prokka_request.other_input_files:
            cmd += ' && prokka ' \
                '--outdir $AZ_BATCH_NODE_MOUNTS_DIR/{fasta} ' \
                '--prefix {prefix} ' \
                '--cpus 8 sequences/{file}'\
                .format(
                    fasta=other_file,
                    prefix=os.path.split(other_file)[1].replace('.fasta', ''),
                    file=os.path.split(other_file)[1]
                )

        # Initialise a list to store the SEQID patterns
        input_file_pattern = []

        # Get all the SEQIDs into the input_file_pattern
        for seqid in prokka_request.seqids:
            input_file_pattern.append(
                'processed-data/{}.fasta '.format(seqid)
            )

        # Submit the request to the AzureBatch service
        generic_api_submit(
            command=cmd,
            container_name=container_name,
            input_file_pattern=input_file_pattern,
            vm_size='Standard_D8s_v3',
            unique_id='FoodPort'
        )

        ProkkaAzureRequest.objects.create(
            prokka_request=prokka_request,
            exit_code_file='NA'
        )
        # # Delete any downloaded fasta files that were used in zip creation if
        # # necessary.
        # fasta_files_to_delete = glob(os.path.join(run_folder, '*.fasta'))
        # for fasta_file in fasta_files_to_delete:
        #     os.remove(fasta_file)
    except Exception as e:
        capture_exception(e)
        prokka_request.status = 'Error'
        prokka_request.save()


@shared_task
def run_sistr(sistr_request_pk):
    sistr_request = 'asdf'  # TODO: Make a sistr request object in models.
    try:
        container_name = 'sistr-{}'.format(sistr_request_pk)
        run_folder = os.path.join('olc_webportalv2/media/{}'.format(container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # TODO: This doesn't actually work right now - there's a .logfile attribute that doesn't get instantiated
        # in the command line call, so crash. Need to update OLCTools
        command = 'source $CONDA/activate /envs/cowbat && python -m spadespipeline.sistr -s sequences {container_name}' \
                  ' && mv sequences {container_name}'.format(container_name=container_name)
        make_config_file(seqids=sistr_request.seqids,
                         job_name=container_name,
                         input_data_folder='sequences',
                         output_data_folder=container_name,
                         command=command,
                         config_file=batch_config_file)
        # With that done, we can submit the file to batch with our package.
        # Use Popen to run in background so that task is considered complete.
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
            vm_size='Standard_D8s_v3',
            no_clean=True,
        )
        # TODO: Have a SISTR request object get created and tracked.
        # Also TODO: add the SISTR request to monitor_tasks in olc_webportalv2/cowbat/tasks
        # Delete any downloaded fasta files that were used in zip creation if necessary.
        fasta_files_to_delete = glob(os.path.join(run_folder, '*.fasta'))
        for fasta_file in fasta_files_to_delete:
            os.remove(fasta_file)

    except:
        sistr_request.status = 'Error'
        sistr_request.save()


@shared_task
def run_amr_summary(amr_summary_pk):
    amr_summary_request = AMRSummary.objects.get(pk=amr_summary_pk)
    try:
        container_name = 'amrsummary-{}'.format(amr_summary_pk)
        run_folder = os.path.join('olc_webportalv2/media/{}'.format(container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        # batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # Delete any downloaded fasta files that were used in zip creation if necessary.
        # fasta_files_to_delete = glob(os.path.join(run_folder, '*.fasta'))
        # for fasta_file in fasta_files_to_delete:
        #     os.remove(fasta_file)
        # click (which geneseekr uses) needs these env vars set or it freaks out.
        # command = 'export LC_ALL=C.UTF-8 && export LANG=C.UTF-8 && ' \
        #           'source $CONDA/activate /envs/cowbat && ' \
        #           'GeneSeekr blastn -u -s sequences -t {resfinder_db} -r sequences/reports -A && ' \
        #           'python -m genemethods.assemblypipeline.mobrecon -s sequences -r {mob_db} && ' \
        #           'mv sequences {container_name}'.format(resfinder_db='/datadrive/0.5.0.23/resfinder',
        #                                                  mob_db='/datadrive/0.5.0.23',
        #                                                  container_name=container_name)
        # command = 'export LC_ALL=C.UTF-8 && export LANG=C.UTF-8 && ' \
        #           'source $CONDA/activate /envs/cowbat && ' \
        #           'GeneSeekr blastn ' \
        #           '-s $AZ_BATCH_TASK_WORKING_DIR/{container_name}/sequences ' \
        #           '-t {resfinder_db} ' \
        #           '-r $AZ_BATCH_TASK_WORKING_DIR/{container_name}/sequences/reports -A && ' \
        #           'python -m spadespipeline.mobrecon ' \
        #           '-s $AZ_BATCH_TASK_WORKING_DIR/{container_name}/sequences -r {mob_db} && ' \
        #           'mv $AZ_BATCH_TASK_WORKING_DIR/{container_name}/sequences {container_name}' \
        #     .format(resfinder_db='/datadrive/0.5.0.23/resfinder',
        #             mob_db='/datadrive/0.5.0.23',
        #             container_name=container_name)
        # make_config_file(seqids=amr_summary_request.seqids,
        #                  job_name=container_name,
        #                  input_data_folder='sequences',
        #                  output_data_folder=container_name,
        #                  command=command,
        #                  config_file=batch_config_file,
        #                  other_input_files=amr_summary_request.other_input_files)
        # With that done, we can submit the file to batch with our package.
        # subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
        #                 '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        # azure_task = AzureBatch()
        # azure_task.main(
        #     configuration_file='{run_folder}/batch_config.txt'.format(run_folder=run_folder),
        #     job_name=container_name,
        #     output_dir='olc_webportalv2/media',
        #     settings=settings,
        #     keep_input_container=True,
        #     download_output_files=False,
        #     vm_size='Standard_D8s_v3',
        #     no_clean=True,
        # )
        # Create the command for the batch request
        cmd = 'export LC_ALL=C.UTF-8 && export LANG=C.UTF-8 && ' \
              'source $CONDA/activate /envs/cowbat && ' \
              'GeneSeekr blastn -u ' \
              '-s $AZ_BATCH_NODE_MOUNTS_DIR/{container}/sequences ' \
              '-t {resfinder_db} ' \
              '-r $AZ_BATCH_NODE_MOUNTS_DIR/{container}/sequences/reports ' \
              '-A && ' \
              'python -m genemethods.assemblypipeline.mobrecon ' \
              '-s $AZ_BATCH_NODE_MOUNTS_DIR/{container}/sequences ' \
              '-r {mob_db} && ' \
              'mv $AZ_BATCH_NODE_MOUNTS_DIR/{container}/sequences ' \
              '$AZ_BATCH_NODE_MOUNTS_DIR/{container}/'.format(
                  resfinder_db='/datadrive/0.5.0.23/resfinder',
                  mob_db='/datadrive/0.5.0.23',
                  container=container_name
               )

        # Initialise a list to store the SEQID patterns
        input_file_pattern = []

        # Get all the SEQIDs into the input_file_pattern
        for seqid in amr_summary_request.seqids:
            input_file_pattern.append(
                'processed-data/{}.fasta '.format(seqid)
            )

        # Submit the command to the AzureBatch service
        generic_api_submit(
            command=cmd,
            input_file_pattern=input_file_pattern,
            container_name=container_name,
            vm_size='Standard_D8s_v3',
            unique_id='FoodPort'
        )
        AMRAzureRequest.objects.create(amr_request=amr_summary_request,
                                       exit_code_file='NA')
        # # Delete any downloaded fasta files that were used in zip creation if
        # # necessary.
        # fasta_files_to_delete = glob(os.path.join(run_folder, '*.fasta'))
        # for fasta_file in fasta_files_to_delete:
        #     os.remove(fasta_file)
    except Exception as e:
        capture_exception(e)
        amr_summary_request.status = 'Error'
        amr_summary_request.save()


@shared_task
def run_mash(tree_request_pk):
    tree_request = Tree.objects.get(pk=tree_request_pk)
    try:
        container_name = 'tree-{}'.format(tree_request_pk)
        run_folder = os.path.join(
            'olc_webportalv2',
            'media',
            '{container}'.format(container=container_name)
        )
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        # Set number of cpus to use/VM size based on how many sequences are input.
        if len(tree_request.seqids) < 10:
            vm_size = 'Standard_D4s_v3'
            cpus = 4
        elif len(tree_request.seqids) < 30:
            vm_size = 'Standard_D8s_v3'
            cpus = 8
        elif len(tree_request.seqids) < 150:
            vm_size = 'Standard_D16s_v3'
            cpus = 16
        else:
            vm_size = 'Standard_D32s_v3'
            cpus = 32
        # # Create our config file for submission to azure batch service.
        # batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # make_config_file(seqids=tree_request.seqids,
        #                  job_name=container_name,
        #                  input_data_folder='sequences',
        #                  output_data_folder=container_name,
        #                  command='source $CONDA/activate /envs/mashtree && mkdir {outdir} && mashtree --numcpus '
        #                          '{cpus} sequences/*.fasta > {outdir}/mash.tree'.format(outdir=container_name,
        #                                                                                 cpus=cpus),
        #                  config_file=batch_config_file,
        #                  vm_size=vm_size,
        #                  other_input_files=tree_request.other_input_files)
        # With that done, we can submit the file to batch with our package.
        # Use Popen to run in background so that task is considered complete.
        # subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
        #                 '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        # azure_task = AzureBatch()
        # azure_task.main(
        #     configuration_file='{run_folder}/batch_config.txt'.format(run_folder=run_folder),
        #     job_name=container_name,
        #     output_dir='olc_webportalv2/media',
        #     settings=settings,
        #     keep_input_container=True,
        #     download_output_files=False,
        #     vm_size='Standard_D8s_v3',
        #     no_clean=True,
        # )

        # Create the command for the batch request
        cmd = 'source $CONDA/activate /envs/mashtree && ' \
              'mkdir $AZ_BATCH_NODE_MOUNTS_DIR/{container}/{outdir} && ' \
              'mashtree --numcpus {cpus} ' \
              '$AZ_BATCH_NODE_MOUNTS_DIR/{container}/sequences/*.fasta > ' \
              '$AZ_BATCH_NODE_MOUNTS_DIR/{container}/{outdir}/mash.tree' \
              .format(
                  container=container_name,
                  outdir=container_name,
                  cpus=cpus
              )

        # Initialise a list to store the SEQID patterns
        input_file_pattern = []

        # Get all the SEQIDs into the input_file_pattern
        for seqid in tree_request.seqids:
            input_file_pattern.append(
                ['processed-data/{sid}.fasta'.format(sid=seqid), 'sequences']
            )

        # Submit the command to the AzureBatch service
        generic_api_submit(
            command=cmd,
            container_name=container_name,
            input_file_pattern=input_file_pattern,
            vm_size=vm_size,
            unique_id='FoodPort'
        )

        TreeAzureRequest.objects.create(
            tree_request=tree_request,
            exit_code_file=os.path.join(run_folder, 'exit_codes.txt')
        )
        # # Delete any downloaded fasta files that were used in zip creation if necessary.
        # fasta_files_to_delete = glob(os.path.join(run_folder, '*.fasta'))
        # for fasta_file in fasta_files_to_delete:
        #     os.remove(fasta_file)

    except Exception as e:
        capture_exception(e)
        tree_request.status = 'Error'
        tree_request.save()


def send_email(subject, body, recipient):
    """
    Sends an email with the given subject, body, and recipient.

    If an "Access denied" SMTP data error or a "wrong version number" SMTP
    server disconnected error occurs, the function will wait for 5 seconds and
    then retry the operation. This retry process will happen up to 50 times.
    If any other error occurs, it will be raised immediately.

    Args:
        subject (str): The subject of the email.
        body (str): The body of the email.
        recipient (str): The recipient's email address.

    Raises:
        smtplib.SMTPDataError: If an SMTP data error occurs that is not an
        "Access denied" error.
        smtplib.SMTPServerDisconnected: If an SMTP server disconnected error
        occurs that is not a "wrong version number" error.
    """
    # Define the sender's email address
    from_addr = \
        'cfia.foodport.donotreply-nepasrepondre.aliport.acia@inspection.gc.ca'
    # Define the recipient's email address
    to_addr = recipient

    # Create a MIME multipart message
    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    # Attempt to send the email up to 50 times
    for _ in range(50):
        try:
            # Connect to the SMTP server
            server = smtplib.SMTP('email-smtp.ca-central-1.amazonaws.com', 587)
            # Start TLS encryption
            server.starttls()
            # Login to the SMTP server
            server.login(
                user=os.environ.get('EMAIL_HOST_USER'),
                password=os.environ.get('EMAIL_HOST_PASSWORD')
            )
            # Convert the message to a string
            text = msg.as_string()
            # Send the email
            server.sendmail(from_addr, to_addr, text)
            # If the email is sent successfully, break out of the loop
            break
        except smtplib.SMTPDataError as e:
            # If an SMTP data error occurs...
            if e.smtp_code == 554 and b"Access denied" in e.smtp_error:
                # If the error is an "Access denied" error, print a message
                # and wait for 5 seconds before retrying
                print("Access denied error occurred, retrying...")
                sleep(5)
            else:
                # If it's a different error, re-raise it
                raise
        except smtplib.SMTPServerDisconnected as e:
            # If the SMTP server gets disconnected...
            if "wrong version number" in str(e):
                # If the error is a "wrong version number" error, print a
                # message, wait for 5 seconds, and reconnect to the server
                print("Wrong version number error occurred, retrying...")
                sleep(5)
            else:
                # If it's a different error, re-raise it
                raise
        finally:
            # Close the connection to the SMTP server
            server.quit()


@shared_task
def run_geneseekr(geneseekr_request_pk):
    """
    This function is a shared task that runs GeneSeekr

    It retrieves a GeneSeekrRequest object from the database using the
    provided primary key, prepares the necessary files and environment for
    running GeneSeekr, and submits the command to the AzureBatch service.
    If the task encounters an error, it captures the exception and
    updates the GeneSeekrRequest object's status and error fields.

    Parameters:
    geneseekr_request_pk (int): The primary key of the GeneSeekrRequest
        object to process.

    Returns:
    None
    """
    geneseekr_request = GeneSeekrRequest.objects.get(pk=geneseekr_request_pk)
    try:
        container_name = 'geneseekr-{pk}'.format(pk=geneseekr_request_pk)
        run_folder = os.path.join(
            'olc_webportalv2',
            'media',
            '{container}'.format(container=container_name)
        )

        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)

        # Create a name to write the user-supplied sequences to file
        file_name = 'query.fasta'
        target_file = os.path.join(run_folder, file_name)

        # Write the sequences to file
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(geneseekr_request.query_sequence)

        # Create the blob client
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )

        # Create the container
        blob_client.create_container(container_name)

        # Upload user-provided file to the container in the targets subfolder
        blob_client.create_blob_from_bytes(
            container_name=container_name,
            blob_name=os.path.join('targets', file_name),
            blob=open(target_file, 'rb').read()
        )

        # Set the size of the batch VM to use based on the number of sequences
        # to process
        if len(geneseekr_request.seqids) < 10:
            vm_size = 'Standard_D4s_v3'
        elif len(geneseekr_request.seqids) < 30:
            vm_size = 'Standard_D8s_v3'
        elif len(geneseekr_request.seqids) < 150:
            vm_size = 'Standard_D16s_v3'
        else:
            vm_size = 'Standard_D32s_v3'
        if geneseekr_request.benchmark:
            vm_size = 'Standard_D32s_v3'

        # Initialise a list to store the input files
        input_file_pattern = []

        # click (which geneseekr uses) needs these environment variables set
        cmd = 'export LC_ALL=C.UTF-8 && export LANG=C.UTF-8 && ' \
              'source $CONDA/activate /envs/cowbat && '

        # Create a variable to store the extremely long path information
        path = '$AZ_BATCH_NODE_MOUNTS_DIR/{container}'.format(
            container=container_name
        )

        # Benchmark datasets are stored in the benchmarks container. These
        # analyses do not have supplied SEQIDs or other_input_files
        if geneseekr_request.benchmark:

            # Initialise a dictionary to look up the benchmark dataset archives
            benchmark_lookup = {
                'Listeria': 'listeria.zip',
                'VTEC': 'vtec.zip'
            }

            # Use the geneseekr_request.benchmark to look up the archive name
            zip_file = benchmark_lookup[geneseekr_request.benchmark]

            # Set the input file information
            input_file_pattern.append(
                'benchmarks/{benchmark}'.format(
                    benchmark=zip_file
                )
            )

            # Update the command with archive decompression, deletion, and
            # GeneSeekr arguments
            cmd += (
                    'mkdir -p /datadrive/{container}/sequences && '
                    'unzip {path}/{zip_file} '
                    '-d /datadrive/{container}/sequences && '
                    'rm {path}/{zip_file} && '
                    'cp -R {path} /datadrive/ && '
                    'GeneSeekr blastn -u '
                    '-s /datadrive/{container}/sequences '
                    '-t /datadrive/{container}/targets '
                    '-r /datadrive/{container}/reports && '
                    'rsync -a /datadrive/{container} $AZ_BATCH_NODE_MOUNTS_DIR'
                    .format(
                        container=container_name,
                        path=path,
                        zip_file=zip_file
                    )
            )

        # All other GeneSeekr requests use the same basic command
        else:

            # If there are lots of SEQIDs, they need to be zipped, and uploaded
            # to the container
            if len(geneseekr_request.seqids) > 50:
                blob_file = zip_files(
                    seqids=geneseekr_request.seqids,
                    target_folder=container_name,
                    container_name='processed-data'
                )

                # Add the zip file to the input files
                input_file_pattern.append(
                    'temporary-storage/{blob_file}'.format(
                        blob_file=blob_file
                    )
                )
                # Update the command with archive decompression, file moving,
                # and archive deletion
                cmd += (
                    'mkdir -p {path}/sequences && '
                    'unzip {path}/{blob_file} -d {path}/sequences &&  '
                    'rm {path}/{blob_file} && '
                    .format(
                        blob_file=blob_file,
                        path=path,
                    )
                )
            else:
                # Get all the SEQIDs into the input_file_pattern
                for seqid in geneseekr_request.seqids:
                    input_file_pattern.append(
                        'processed-data/{}.fasta sequences'.format(seqid)
                    )

            # Add the GeneSeekr portion of the command
            cmd += (
                'GeneSeekr blastn -u '
                '-s {path}/sequences '
                '-t {path}/targets '
                '-r {path}/reports'.format(
                    path=path
                )
            )

        # Submit the command to the AzureBatch service
        generic_api_submit(
            command=cmd,
            container_name=container_name,
            input_file_pattern=input_file_pattern,
            vm_size=vm_size,
            unique_id='FoodPort'
        )
        GeneSeekrAzureRequest.objects.create(
            geneseekr_request=geneseekr_request,
            exit_code_file='NA'
        )
    except Exception as e:
        capture_exception(e)
        geneseekr_request.status = 'Error'
        geneseekr_request.error = e
        geneseekr_request.save()


#################### NEAREST NEIGHBORS TASK ##############################
@shared_task
def run_nearest_neighbors(nearest_neighbor_pk):
    nearest_neighbor_request = NearestNeighbors.objects.get(pk=nearest_neighbor_pk)
    try:
        work_dir = 'olc_webportalv2/media/neighbor-{}'.format(nearest_neighbor_pk)
        if not os.path.isdir(work_dir):
            os.makedirs(work_dir)
        seqids_in_metadata = list()
        for sequence_data in SequenceData.objects.filter():
            seqids_in_metadata.append(sequence_data.seqid)
        # Download requested SeqID from blob storage - we *should* have already validated that the sequence exists.
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        if nearest_neighbor_request.seqid != '':
            fasta_file = os.path.join(work_dir, nearest_neighbor_request.seqid + '.fasta')
            # TODO: Check what error happens here if blob doesn't actually exist and catch, or verify that blob exists before
            #  trying to retrieve it.
            blob_client.get_blob_to_path(container_name='processed-data',
                                         blob_name=nearest_neighbor_request.seqid + '.fasta',
                                         file_path=fasta_file)
        else:
            fasta_file = os.path.join(work_dir, nearest_neighbor_request.uploaded_file_name)
            blob_client.get_blob_to_path(container_name='neighbor-{}'.format(nearest_neighbor_request.pk),
                                         blob_name=nearest_neighbor_request.uploaded_file_name,
                                         file_path=fasta_file)

        mash_output_file = os.path.join(work_dir, 'mash_dist_results.tsv')
        cmd = '/data/web/mash-Linux64-v2.1/mash dist {query} {sketch} > {output}'.format(query=fasta_file,
                                                                                         # TODO: Actually install mash in dockerfile
                                                                                         sketch='/data/web/sketchomatic.msh',
                                                                                         # TODO: Change me!
                                                                                         output=mash_output_file)
        os.system(cmd)  # Subprocess doesn't work here. Should be OK to switch once mash is actually installed.
        shutil.make_archive(work_dir, 'zip', work_dir)
        sas_url = generate_download_link(blob_client=blob_client,
                                         container_name='neighbor-output-{}'.format(nearest_neighbor_pk),
                                         output_zipfile=work_dir + '.zip',
                                         expiry=8)
        nearest_neighbor_request.download_link = sas_url
        distances = dict()
        with open(mash_output_file) as f:
            for line in f:
                x = line.split()
                query_seqid = x[1].split('/')[-1].replace('.fasta', '')
                query_distance = float(x[2])
                # Don't show the fact we get match to self. Not useful info
                if query_seqid != nearest_neighbor_request.seqid and query_seqid in seqids_in_metadata:
                    distances[query_seqid] = query_distance
        sorted_distances = sorted(distances.items(), key=lambda kv: kv[1])
        for i in range(nearest_neighbor_request.number_neighbors):
            NearNeighborDetail.objects.create(near_neighbor_request=nearest_neighbor_request,
                                              seqid=sorted_distances[i][0],
                                              distance=sorted_distances[i][1])
        shutil.rmtree(work_dir)
        os.remove(work_dir + '.zip')
        nearest_neighbor_request.status = 'Complete'
        nearest_neighbor_request.save()
    except Exception as e:
        capture_exception(e)
        nearest_neighbor_request.status = 'Error'
        nearest_neighbor_request.save()
