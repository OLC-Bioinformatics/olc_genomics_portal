# Django-related imports
from background_task import background
from olc_webportalv2.cowbat.models import SequencingRun, AzureTask
# For some reason settings get imported from base.py - in views they come from prod.py. Weird.
from django.conf import settings  # To access azure credentials
# Standard python stuff
import subprocess
import datetime
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


@background(schedule=1)
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
            f.write('CLOUDIN:={} {}\n'.format(os.path.join(container_name, 'InterOp', '*.bin'), os.path.join(str(sequencing_run), 'InterOp')))
            f.write('OUTPUT:={}\n'.format(str(sequencing_run) + '/'))
            # The CLARK part of the pipeline needs absolute path specified, so the $AZ_BATCH_TASK_WORKING_DIR has to
            # be specified as part of the command in order to have the absolute path of our sequences propagate to it.
            f.write('COMMAND:=source $CONDA/activate /envs/cowbat && assembly_pipeline.py '
                    '-s $AZ_BATCH_TASK_WORKING_DIR/{run_name} -r /databases/0.3.4\n'.format(run_name=str(sequencing_run)))

        # With that done, we can submit the file to batch with our package.
        # Use Popen to run in background so that task is considered complete.
        subprocess.Popen('AzureBatch -k -d -e {run_folder}/exit_codes.txt -c {run_folder}/batch_config.txt '
                         '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        AzureTask.objects.create(sequencing_run=sequencing_run,
                                 exit_code_file=os.path.join(run_folder, 'exit_codes.txt'))
    except:
        """
        send_email(subject='Assembly Error - Run {} was not successfully submitted to Azure Batch.'.format(str(sequencing_run)),
                   body='Fix it!',
                   recipient='andrew.low@canada.ca')
        """
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


@background(schedule=1)
def cowbat_cleanup(sequencing_run_pk):
    sequencing_run = SequencingRun.objects.get(pk=sequencing_run_pk)
    print('Cleaning up run {}'.format(sequencing_run.run_name))
    # With the sequencing run done, need to put create a zipfile with assemblies and reports for user to download.
    # First create a folder.
    run_folder = 'olc_webportalv2/media/{run_name}'.format(run_name=str(sequencing_run))
    reports_and_assemblies_folder = 'olc_webportalv2/media/{run_name}/reports_and_assemblies'.format(run_name=str(sequencing_run))
    if not os.path.isdir(reports_and_assemblies_folder):
        os.makedirs(reports_and_assemblies_folder)
    container_name = sequencing_run.run_name.lower().replace('_', '-') + '-output'
    blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                   account_key=settings.AZURE_ACCOUNT_KEY)
    # Download all reports and assemblies to reports and assemblies folder.
    assemblies_folder = 'olc_webportalv2/media/{run_name}/reports_and_assemblies/BestAssemblies'.format(run_name=str(sequencing_run))
    reports_folder = 'olc_webportalv2/media/{run_name}/reports_and_assemblies/reports'.format(run_name=str(sequencing_run))
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

    # With that done, create a zipfile.
    blob_name = sequencing_run.run_name.lower().replace('_', '-') + '.zip'
    shutil.make_archive(os.path.join(run_folder, sequencing_run.run_name.lower().replace('_', '-')), 'zip', reports_and_assemblies_folder)
    report_assembly_container = 'reports-and-assemblies'
    blob_client.create_container(report_assembly_container)
    blob_client.create_blob_from_path(container_name=report_assembly_container,
                                      blob_name=blob_name,
                                      file_path=os.path.join(run_folder, blob_name))
    # Upload zipfile to cloud, and create an SAS link that lasts a long time (a year?)
    sas_token = blob_client.generate_container_shared_access_signature(container_name=report_assembly_container,
                                                                       permission=BlobPermissions.READ,
                                                                       expiry=datetime.datetime.utcnow() + datetime.timedelta(days=365))
    sas_url = blob_client.make_blob_url(container_name=report_assembly_container,
                                        blob_name=blob_name,
                                        sas_token=sas_token)
    SequencingRun.objects.filter(pk=sequencing_run_pk).update(download_link=sas_url)
    shutil.rmtree(os.path.join('olc_webportalv2/media/{run_name}'.format(run_name=str(sequencing_run))))
    # Run is now considered complete! Update to let user know and send email to people that need to know.
    SequencingRun.objects.filter(pk=sequencing_run_pk).update(status='Complete')
    print('Complete!')
    # Finally (but actually this time) send an email to relevant people to let them know that things have worked.
    # Uncomment this on the cloud where email sending actually works
    recipient_list = ['paul.manninger@canada.ca', 'andrew.low@canada.ca', 'adam.koziol@canada.ca']
    """
    for recipient in recipient_list:
        send_email(subject='Run {} has finished assembly.'.format(str(sequencing_run)),
                   body='If you are Andrew or Adam, please download the blob container to local OLC storage. '
                        'If you\'re Paul, please add this data to the OLC database.\n Reports and assemblies '
                        'are available at the following link: {}'.format(sas_url),
                   recipient=recipient)
    """
