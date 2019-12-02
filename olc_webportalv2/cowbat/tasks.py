# Django-related imports
from olc_webportalv2.cowbat.models import SequencingRun, AzureTask
from olc_webportalv2.geneseekr.models import TreeAzureRequest, Tree, AMRSummary, AMRAzureRequest, \
    AMRDetail, ProkkaRequest, ProkkaAzureRequest
from olc_webportalv2.vir_typer.models import VirTyperAzureRequest, VirTyperProject
# For some reason settings get imported from base.py - in views they come from prod.py. Weird.
from django.conf import settings  # To access azure credentials
from django.core.mail import send_mail  # To be used eventually, only works in cloud
# Standard python stuff
import subprocess
import datetime
from datetime import timezone
import csv
# For whatever reason tasks.py doesn't get django settings properly, so send_mail from django doesn't work.
# Use SMTPlib combined with os.environ.get to get around this.
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import fnmatch
import shutil
import os
from azure.storage.blob import BlockBlobService
from azure.storage.blob import BlobPermissions
import azure.batch.batch_service_client as batch
import azure.batch.batch_auth as batch_auth
import azure.batch.models as batchmodels
from strainchoosr import strainchoosr
import re
import ete3
import json
# Celery Task Management
from celery import shared_task, task
# Sentry
from sentry_sdk import capture_exception
from azure.batch.models import BatchErrorException


@shared_task
def run_cowbat_batch(sequencing_run_pk):
    try:
        blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                       account_name=settings.AZURE_ACCOUNT_NAME)
        sequencing_run = SequencingRun.objects.get(pk=sequencing_run_pk)
        # Check that all files are actually present. If not, some upload managed to fail.
        # Change status to 'UploadError', which will allow user to retry the upload.
        run_folder = 'olc_webportalv2/media/{run_name}'.format(run_name=str(sequencing_run))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        container_name = sequencing_run.run_name.lower().replace('_', '-')
        blob_filenames = list()
        blobs = blob_client.list_blobs(container_name=container_name)
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

        # Create a configuration file to be used by my Azure batch script.
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        with open(batch_config_file, 'w') as f:
            f.write('BATCH_ACCOUNT_NAME:={}\n'.format(settings.BATCH_ACCOUNT_NAME))
            f.write('BATCH_ACCOUNT_KEY:={}\n'.format(settings.BATCH_ACCOUNT_KEY))
            f.write('BATCH_ACCOUNT_URL:={}\n'.format(settings.BATCH_ACCOUNT_URL))
            f.write('STORAGE_ACCOUNT_NAME:={}\n'.format(settings.AZURE_ACCOUNT_NAME))
            f.write('STORAGE_ACCOUNT_KEY:={}\n'.format(settings.AZURE_ACCOUNT_KEY))
            f.write('JOB_NAME:={}\n'.format(container_name))
            f.write('VM_IMAGE:={}\n'.format(settings.VM_IMAGE))
            f.write('VM_CLIENT_ID:={}\n'.format(settings.VM_CLIENT_ID))
            f.write('VM_SECRET:={}\n'.format(settings.VM_SECRET))
            f.write('VM_TENANT:={}\n'.format(settings.VM_TENANT))
            f.write('CLOUDIN:={} {}\n'.format(os.path.join(container_name, '*.fastq.gz'), str(sequencing_run)))
            f.write('CLOUDIN:={} {}\n'.format(os.path.join(container_name, '*.xml'), str(sequencing_run)))
            f.write('CLOUDIN:={} {}\n'.format(os.path.join(container_name, '*.csv'), str(sequencing_run)))
            f.write('CLOUDIN:={} {}\n'.format(os.path.join(container_name, 'InterOp', '*.bin'),
                                              os.path.join(str(sequencing_run), 'InterOp')))
            f.write('OUTPUT:={}\n'.format(str(sequencing_run) + '/'))
            # The CLARK part of the pipeline needs absolute path specified, so the $AZ_BATCH_TASK_WORKING_DIR has to
            # be specified as part of the command in order to have the absolute path of our sequences propagate to it.
            f.write('COMMAND:=source $CONDA/activate /envs/cowbat && assembly_pipeline.py '
                    '-s $AZ_BATCH_TASK_WORKING_DIR/{run_name} -r /databases/0.5.0.12.0\n'
                    .format(run_name=str(sequencing_run)))

        # With that done, we can submit the file to batch with our package.
        # Use Popen to run in background so that task is considered complete.
        subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
                        '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        AzureTask.objects.create(sequencing_run=sequencing_run,
                                 exit_code_file=os.path.join(run_folder, 'exit_codes.txt'))
    except Exception as e:
        capture_exception(e)
        SequencingRun.objects.filter(pk=sequencing_run_pk).update(status='Error')


def send_email(subject, body, recipient):
    fromaddr = os.environ.get('EMAIL_HOST_USER')
    toaddr = recipient
    msg = MIMEMultipart()
    msg['From'] = fromaddr
    msg['To'] = toaddr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(user=os.environ.get('EMAIL_HOST_USER'), password=os.environ.get('EMAIL_HOST_PASSWORD'))
    text = msg.as_string()
    server.sendmail(fromaddr, toaddr, text)


@shared_task
def cowbat_cleanup(sequencing_run_pk):
    sequencing_run = SequencingRun.objects.get(pk=sequencing_run_pk)
    print('Cleaning up run {}'.format(sequencing_run.run_name))
    # With the sequencing run done, need to put create a zipfile with assemblies and reports for user to download.
    # First create a folder.
    run_folder = 'olc_webportalv2/media/{run_name}'.format(run_name=str(sequencing_run))
    reports_and_assemblies_folder = 'olc_webportalv2/media/{run_name}/reports_and_assemblies'\
        .format(run_name=str(sequencing_run))
    if not os.path.isdir(reports_and_assemblies_folder):
        os.makedirs(reports_and_assemblies_folder)
    container_name = sequencing_run.run_name.lower().replace('_', '-') + '-output'
    blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                   account_key=settings.AZURE_ACCOUNT_KEY)
    # Download all reports and assemblies to reports and assemblies folder.
    assemblies_folder = 'olc_webportalv2/media/{run_name}/reports_and_assemblies/BestAssemblies'\
        .format(run_name=str(sequencing_run))
    reports_folder = 'olc_webportalv2/media/{run_name}/reports_and_assemblies/reports'\
        .format(run_name=str(sequencing_run))
    if not os.path.isdir(assemblies_folder):
        os.makedirs(assemblies_folder)
    if not os.path.isdir(reports_folder):
        os.makedirs(reports_folder)
    # List all the things in the container - if it's a file in reports folder or an assembly, download it.
    blobs = blob_client.list_blobs(container_name=container_name)
    for blob in blobs:
        if fnmatch.fnmatch(blob.name, os.path.join(sequencing_run.run_name, 'BestAssemblies/*.fasta')):
            blob_client.get_blob_to_path(container_name=container_name,
                                         blob_name=blob.name,
                                         file_path=os.path.join(assemblies_folder, os.path.split(blob.name)[1]))
        elif fnmatch.fnmatch(blob.name, os.path.join(sequencing_run.run_name, 'reports/*.csv')):
            blob_client.get_blob_to_path(container_name=container_name,
                                         blob_name=blob.name,
                                         file_path=os.path.join(reports_folder, os.path.split(blob.name)[1]))
        elif fnmatch.fnmatch(blob.name, os.path.join(sequencing_run.run_name, 'reports/*.fa')):
            blob_client.get_blob_to_path(container_name=container_name,
                                         blob_name=blob.name,
                                         file_path=os.path.join(reports_folder, os.path.split(blob.name)[1]))
        elif fnmatch.fnmatch(blob.name, os.path.join(sequencing_run.run_name, 'reports/*.xlsx')):
            blob_client.get_blob_to_path(container_name=container_name,
                                         blob_name=blob.name,
                                         file_path=os.path.join(reports_folder, os.path.split(blob.name)[1]))
        # Also get the SampleSheet put into the reports folder.
        elif fnmatch.fnmatch(blob.name, os.path.join(sequencing_run.run_name, 'SampleSheet.csv')):
            blob_client.get_blob_to_path(container_name=container_name,
                                         blob_name=blob.name,
                                         file_path=os.path.join(reports_folder, os.path.split(blob.name)[1]))

    # With that done, create a zipfile.
    blob_name = sequencing_run.run_name.lower().replace('_', '-') + '.zip'
    shutil.make_archive(os.path.join(run_folder, sequencing_run.run_name.lower().replace('_', '-')), 'zip',
                        reports_and_assemblies_folder)
    report_assembly_container = 'reports-and-assemblies'
    sas_url = generate_download_link(blob_client=blob_client,
                                     container_name=report_assembly_container,
                                     output_zipfile=os.path.join(run_folder, blob_name),
                                     expiry=730)
    SequencingRun.objects.filter(pk=sequencing_run_pk).update(download_link=sas_url)
    shutil.rmtree(os.path.join('olc_webportalv2/media/{run_name}'.format(run_name=str(sequencing_run))))
    # Run is now considered complete! Update to let user know and send email to people that need to know.
    SequencingRun.objects.filter(pk=sequencing_run_pk).update(status='Complete')
    print('Complete!')
    # Finally (but actually this time) send an email to relevant people to let them know that things have worked.
    # Uncomment this on the cloud where email sending actually works
    realtime_strains = list()
    for seqid in sequencing_run.realtime_strains:
        if sequencing_run.realtime_strains[seqid] == 'True':
            realtime_strains.append(seqid)
    recipient_list = ['ray.allain@canada.ca', 'adam.koziol@canada.ca', 'julie.shay@canada.ca']
    """
    for recipient in recipient_list:
        send_email(subject='Run {} has finished assembly.'.format(str(sequencing_run)),
                   body='If you are Adam or Julie, please download the blob container to local OLC storage. '
                        'If you\'re Ray, please add this data to the OLC database.\n Reports and assemblies '
                        'are available at the following link: {}\nIn this run, the following strains '
                        'will need ROGAs created: {}'.format(sas_url, realtime_strains),
                   recipient=recipient)
    """


def escape_ansi(line):
    ansi_escape = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')
    return ansi_escape.sub('', line)


def check_cowbat_progress(batch_client, job_id, sequencing_run):
    """

    :param batch_client:
    :param job_id:
    :param sequencing_run:
    :return:
    """
    node_files = batch_client.file.list_from_task(job_id=job_id, task_id=job_id, recursive=True)
    contents = dict()
    try:
        for node_file in node_files:
            # Stderr.txt file
            if 'stderr' in node_file.name:
                try:
                    contents[node_file.name] = batch_client.file.get_from_task(job_id=job_id, task_id=job_id,
                                                                               file_path=node_file.name)
                except:
                    pass
    except BatchErrorException:
        pass
    for file_name, content_object in contents.items():
        for content_chunk in content_object:

            # print(str(content_chunk.decode()))
            try:
                clean_line = escape_ansi(line=content_chunk.decode())
                final_line = clean_line.split('\n')[-2]
                status = ' '.join(final_line.split(' ')[2:])
                sequencing_run.progress = status
                sequencing_run.save()
            except:
                pass


def check_cowbat_tasks():
    # Check for completed cowbat runs
    azure_tasks = AzureTask.objects.filter()
    # Create batch client so we can check on the status of runs.
    credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
    batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
    for task in azure_tasks:
        sequencing_run = SequencingRun.objects.get(pk=task.sequencing_run.pk)
        batch_job_name = sequencing_run.run_name.lower().replace('_', '-')
        # Check if all tasks (within batch, this terminology gets confusing) associated
        # with this job have completed.
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        except BatchErrorException:
            pass
        # Assuming that things have completed, check exit codes. Set status to error if any are non-zero.
        if tasks_completed:
            exit_codes_good = True
            try:
                for cloudtask in batch_client.task.list(batch_job_name):
                    if cloudtask.execution_info.exit_code != 0:
                        exit_codes_good = False
            except BatchErrorException:
                pass
            try:
                batch_client.job.delete(job_id=batch_job_name)
                batch_client.pool.delete(pool_id=batch_job_name)  # Set up in tasks.py so that pool and job have same ID
            except BatchErrorException:
                # Delete task so we don't have to keep checking up on it.
                AzureTask.objects.filter(id=task.id).delete()
                cowbat_cleanup.apply_async(queue='cowbat', args=(sequencing_run.pk,))
                # Something went wrong - update status to error so user knows.
                SequencingRun.objects.filter(pk=sequencing_run.pk).update(status='Error')
            if exit_codes_good:
                # Get rid of job and pool so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
                # This also sets task to complete
                cowbat_cleanup.apply_async(queue='cowbat', args=(sequencing_run.pk, ))
            else:
                # Something went wrong - update status to error so user knows.
                SequencingRun.objects.filter(pk=sequencing_run.pk).update(status='Error')
            # Delete task so we don't have to keep checking up on it.
            AzureTask.objects.filter(id=task.id).delete()
        else:
            check_cowbat_progress(batch_client, batch_job_name, sequencing_run)


def check_tree_tasks():
    # Also check for Mash tree creation tasks
    tree_tasks = TreeAzureRequest.objects.filter()
    credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
    batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
    for task in tree_tasks:
        tree_task = Tree.objects.get(pk=task.tree_request.pk)
        batch_job_name = 'mash-{}'.format(task.tree_request.pk)
        # Check if tasks related with this mash job have finished.
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False

        except:  # If something errors first time through job doesn't exist. In that case, give up.
            Tree.objects.filter(pk=task.tree_request.pk).update(status='Error')
            # Delete task so we don't keep iterating over it.
            TreeAzureRequest.objects.filter(id=task.id).delete()
            continue
        # If tasks have completed, check if they were successful.
        if tasks_completed:
            exit_codes_good = True
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.execution_info.exit_code != 0:
                    exit_codes_good = False
            # Get rid of job and pool so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)
            if exit_codes_good:
                # Now need to generate an SAS URL and give access to it/update the download link.
                blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                               account_name=settings.AZURE_ACCOUNT_NAME)
                # Download the output container so we can zip it.
                download_container(blob_service=blob_client,
                                   container_name=batch_job_name + '-output',
                                   output_dir='olc_webportalv2/media')
                tree_file = 'olc_webportalv2/media/mash-{}/mash.tree'.format(tree_task.pk)
                with open(tree_file) as f:
                    tree_string = f.readline()
                if tree_task.number_diversitree_strains > 0:
                    diverse_strains = strainchoosr.pd_greedy(tree=ete3.Tree(tree_file),
                                                             number_tips=tree_task.number_diversitree_strains,
                                                             starting_strains=[])
                    tree_task.seqids_diversitree = strainchoosr.get_leaf_names_from_nodes(diverse_strains)
                tree_task.newick_tree = tree_string.rstrip().replace("'", "")
                blob_client.delete_container(container_name=batch_job_name)
                # Should now have results from mash in olc_webportalv2/media/mash-X, where X is pk of tree request
                tree_output_folder = os.path.join('olc_webportalv2/media', batch_job_name)
                os.remove(os.path.join(tree_output_folder, 'batch_config.txt'))
                # Need to zip this folder and then upload the zipped folder to cloud
                shutil.make_archive(tree_output_folder, 'zip', tree_output_folder)
                tree_result_container = 'tree-{}'.format(tree_task.pk)
                sas_url = generate_download_link(blob_client=blob_client,
                                                 container_name=tree_result_container,
                                                 output_zipfile=tree_output_folder + '.zip',
                                                 expiry=8)
                shutil.rmtree(tree_output_folder)
                zip_folder = 'olc_webportalv2/media/{}.zip'.format(batch_job_name)
                if os.path.isfile(zip_folder):
                    os.remove(zip_folder)
                tree_task.download_link = sas_url
                tree_task.status = 'Complete'
                tree_task.save()

            else:
                Tree.objects.filter(pk=task.tree_request.pk).update(status='Error')
            # Delete task so we don't keep iterating over it.
            TreeAzureRequest.objects.filter(id=task.id).delete()


def check_amr_summary_tasks():
    amr_summary_tasks = AMRAzureRequest.objects.filter()
    credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
    batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
    for task in amr_summary_tasks:
        amr_task = AMRSummary.objects.get(pk=task.amr_request.pk)
        batch_job_name = 'amrsummary-{}'.format(task.amr_request.pk)
        # Check if tasks related with this amrsummary job have finished.
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        except:  # If something errors first time through job can't get deleted. In that case, give up.
            AMRSummary.objects.filter(pk=task.amr_request.pk).update(status='Error')
            # Delete task so we don't keep iterating over it.
            AMRAzureRequest.objects.filter(id=task.id).delete()
            continue
        # If tasks have completed, check if they were successful.
        if tasks_completed:
            exit_codes_good = True
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.execution_info.exit_code != 0:
                    exit_codes_good = False
            # Get rid of job and pool so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)
            if exit_codes_good:
                # Now need to generate an SAS URL and give access to it/update the download link.
                blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                               account_name=settings.AZURE_ACCOUNT_NAME)
                # Download the output container so we can zip it.
                download_container(blob_service=blob_client,
                                   container_name=batch_job_name + '-output',
                                   output_dir='olc_webportalv2/media')
                output_dir = 'olc_webportalv2/media/{}'.format(batch_job_name)
                if os.path.isfile(os.path.join(output_dir, 'batch_config.txt')):
                    os.remove(os.path.join(output_dir, 'batch_config.txt'))
                shutil.make_archive(output_dir, 'zip', output_dir)
                amr_result_container = 'amrsummary-{}'.format(amr_task.pk)
                sas_url = generate_download_link(blob_client=blob_client,
                                                 container_name=amr_result_container,
                                                 output_zipfile=output_dir + '.zip',
                                                 expiry=8)
                # Also need to populate our AMRDetail model with results.
                seq_amr_dict = dict()
                for seqid in amr_task.seqids:
                    seq_amr_dict[seqid] = dict()
                with open(os.path.join(output_dir, 'reports', 'amr_summary.csv')) as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        seqid = row['Strain']
                        gene = row['Gene']
                        location = row['Location']
                        if seqid not in seq_amr_dict:
                            seq_amr_dict[seqid] = dict()
                        seq_amr_dict[seqid][gene] = location
                for seqid in seq_amr_dict:
                    AMRDetail.objects.create(amr_request=amr_task,
                                             seqid=seqid,
                                             amr_results=seq_amr_dict[seqid])
                # Finally, do some cleanup
                shutil.rmtree(output_dir)
                os.remove(output_dir + '.zip')
                amr_task.download_link = sas_url
                amr_task.status = 'Complete'
                amr_task.save()

            else:
                amr_task.status = 'Error'
                amr_task.save()
            AMRAzureRequest.objects.filter(id=task.id).delete()


def check_vir_typer_tasks():
    """
    VirusTyper!
    """

    vir_typer_tasks = VirTyperAzureRequest.objects.filter()
    credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
    batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
    for sub_task in vir_typer_tasks:
        vir_typer_task = VirTyperProject.objects.get(pk=sub_task.project_name.pk)
        batch_job_name = VirTyperProject.objects.get(pk=vir_typer_task.pk).container_namer()
        # Check if tasks related with this VirusTyper project have finished.
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        except:  # If something errors first time through job can't get deleted. In that case, give up.
            VirTyperProject.objects.filter(pk=vir_typer_task.pk).update(status='Error')
            # Delete Azure task so we don't keep iterating over it.
            VirTyperAzureRequest.objects.filter(id=sub_task.id).delete()
            continue
        # If tasks have completed, check if they were successful.
        if tasks_completed:
            exit_codes_good = True
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.execution_info.exit_code != 0:
                    exit_codes_good = False
            # Get rid of job and pool so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)
            if exit_codes_good:
                # Now need to generate an SAS URL and give access to it/update the download link.
                blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                               account_name=settings.AZURE_ACCOUNT_NAME)
                vir_typer_result_container = batch_job_name + '-output'
                #

                # Download the output container so we can zip it.
                download_container(blob_service=blob_client,
                                   container_name=vir_typer_result_container,
                                   output_dir='olc_webportalv2/media')
                output_dir = 'olc_webportalv2/media/{}'.format(batch_job_name)
                if os.path.isfile(os.path.join(output_dir, 'batch_config.txt')):
                    os.remove(os.path.join(output_dir, 'batch_config.txt'))
                shutil.make_archive(output_dir, 'zip', output_dir)
                # Read in the json output
                json_output = os.path.join(output_dir, 'virus_typer_outputs.json')
                with open(json_output, 'r') as json_report:

                    vir_typer_task.report = json.load(json_report)
                # vir_typer_result_container = 'vir-typer-result-{}'.format(vir_typer_task.pk)
                sas_url = generate_download_link(blob_client=blob_client,
                                                 container_name=vir_typer_result_container,
                                                 output_zipfile=output_dir + '.zip',
                                                 expiry=8)
                vir_typer_task.download_link = sas_url
                vir_typer_task.status = 'Complete'
                vir_typer_task.save()
                shutil.rmtree(output_dir)
                os.remove(output_dir + '.zip')
            else:
                vir_typer_task.status = 'Error'
                vir_typer_task.save()
            VirTyperAzureRequest.objects.filter(id=sub_task.id).delete()


def check_prokka_tasks():
    # Prokka!
    prokka_tasks = ProkkaAzureRequest.objects.filter()
    credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
    batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
    for task in prokka_tasks:
        prokka_task = ProkkaRequest.objects.get(pk=task.prokka_request.pk)
        batch_job_name = 'prokka-{}'.format(task.prokka_request.pk)
        # Check if tasks related with this amrsummary job have finished.
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        except:  # If something errors first time through job can't get deleted. In that case, give up.
            ProkkaRequest.objects.filter(pk=task.prokka_request.pk).update(status='Error')
            # Delete task so we don't keep iterating over it.
            ProkkaAzureRequest.objects.filter(id=task.id).delete()
            continue
        # If tasks have completed, check if they were successful.
        if tasks_completed:
            exit_codes_good = True
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.execution_info.exit_code != 0:
                    exit_codes_good = False
            # Get rid of job and pool so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)
            if exit_codes_good:
                # Now need to generate an SAS URL and give access to it/update the download link.
                blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                               account_name=settings.AZURE_ACCOUNT_NAME)
                # Download the output container so we can zip it.
                download_container(blob_service=blob_client,
                                   container_name=batch_job_name + '-output',
                                   output_dir='olc_webportalv2/media')
                output_dir = 'olc_webportalv2/media/{}'.format(batch_job_name)
                if os.path.isfile(os.path.join(output_dir, 'batch_config.txt')):
                    os.remove(os.path.join(output_dir, 'batch_config.txt'))
                shutil.make_archive(output_dir, 'zip', output_dir)
                prokka_result_container = 'prokka-result-{}'.format(prokka_task.pk)
                sas_url = generate_download_link(blob_client=blob_client,
                                                 container_name=prokka_result_container,
                                                 output_zipfile=output_dir + '.zip',
                                                 expiry=8)
                prokka_task.download_link = sas_url
                prokka_task.status = 'Complete'
                prokka_task.save()
                shutil.rmtree(output_dir)
                os.remove(output_dir + '.zip')
            else:
                prokka_task.status = 'Error'
                prokka_task.save()
            ProkkaAzureRequest.objects.filter(id=task.id).delete()


@shared_task
def monitor_tasks():
    # Keep track of jobs that have been submitted to Azure Batch Service.
    # Call each type of task we submit to Batch separately, and have sentry tell us if anything goes wrong.

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


def generate_download_link(blob_client, container_name, output_zipfile, expiry=8):
    """
    Make a download link for a file that will be put into Azure blob storage, good for up to expiry days
    :param blob_client: Instance of azure.storage.blob.BlockBlobService
    :param container_name: Name of container you want to create.
    :param output_zipfile: Zipfile you want to upload and create a link for.
    :param expiry: Number of days link should be valid for.
    :return: String of a link that allows people to download container.
    """
    blob_client.create_container(container_name)
    blob_name = os.path.split(output_zipfile)[1]
    blob_client.create_blob_from_path(container_name=container_name,
                                      blob_name=blob_name,
                                      file_path=output_zipfile)
    sas_token = blob_client.generate_container_shared_access_signature(container_name=container_name,
                                                                       permission=BlobPermissions.READ,
                                                                       expiry=datetime.datetime.utcnow() + datetime
                                                                       .timedelta(days=expiry))
    sas_url = blob_client.make_blob_url(container_name=container_name,
                                        blob_name=blob_name,
                                        sas_token=sas_token)
    return sas_url


def download_container(blob_service, container_name, output_dir):
    # Modified from:
    # https://blogs.msdn.microsoft.com/brijrajsingh/2017/05/27/downloading-a-azure-blob-storage-container-python/
    generator = blob_service.list_blobs(container_name)
    for blob in generator:
        # check if the path contains a folder structure, create the folder structure
        if "/" in blob.name:
            # extract the folder path and check if that folder exists locally, and if not create it
            head, tail = os.path.split(blob.name)
            if os.path.isdir(os.path.join(output_dir, head)):
                # download the files to this directory
                blob_service.get_blob_to_path(container_name, blob.name, os.path.join(output_dir, head, tail))
            else:
                # create the diretcory and download the file to it
                os.makedirs(os.path.join(output_dir, head))
                blob_service.get_blob_to_path(container_name, blob.name, os.path.join(output_dir, head, tail))
        else:
            blob_service.get_blob_to_path(container_name, blob.name, os.path.join(output_dir, blob.name))


@shared_task
def clean_old_containers():
    blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                   account_key=settings.AZURE_ACCOUNT_KEY)
    # Patterns we have to worry about - data-request-digits, geneseekr-digits
    # TODO: Add more of these as more analysis types get created.
    patterns_to_search = ['^data-request-\d+$', '^geneseekr-\d+$', 'amrsummary-\d+$',
                          '^amrsummary-\d+-output$', '^prokka-\d+$', '^mash-\d+$',
                          '^prokka-\d+-output$', '^mash-\d+-output$', '^prokka-result-\d+$',
                          '^tree-\d+$']
    generator = blob_client.list_containers(include_metadata=True)
    for container in generator:
        for pattern in patterns_to_search:
            if re.match(pattern, container.name):
                today = datetime.datetime.now(timezone.utc)
                container_age = abs(container.properties.last_modified - today).days
                if container_age > 7:
                    blob_client.delete_container(container.name)
