from django.core.management.base import BaseCommand
from olc_webportalv2.cowbat.models import AzureTask, SequencingRun
from olc_webportalv2.geneseekr.models import ParsnpAzureRequest, ParsnpTree
from django.core.mail import send_mail  # To be used eventually, only works in cloud
from django.conf import settings
from olc_webportalv2.cowbat.tasks import cowbat_cleanup
import datetime
import shutil
import time
import os

from azure.storage.blob import BlockBlobService
from azure.storage.blob import BlobPermissions
import azure.batch.batch_service_client as batch
import azure.batch.batch_auth as batch_auth
import azure.batch.models as batchmodels


def monitor_tasks():
    while True:
        # Check for completed cowbat runs
        azure_tasks = AzureTask.objects.filter()
        # Create batch client so we can check on the status of runs.
        credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
        batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
        for task in azure_tasks:
            sequencing_run = SequencingRun.objects.get(pk=task.sequencing_run.pk)
            batch_job_name = sequencing_run.run_name.lower().replace('_', '-')
            with open('debug.txt', 'a+') as f:
                f.write('Job name: {}\n'.format(batch_job_name))
            # Check if all tasks (within batch, this terminology gets confusing) associated
            # with this job have completed.
            tasks_completed = True
            for cloudtask in batch_client.task.list(batch_job_name):
                with open('debug.txt', 'a+') as f:
                    f.write('Task state: {}\n'.format(cloudtask.state))
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False

            # Assuming that things have completed, check exit codes. Set status to error if any are non-zero.
            if tasks_completed:
                exit_codes_good = True
                for cloudtask in batch_client.task.list(batch_job_name):
                    with open('debug.txt', 'a+') as f:
                        f.write('exit code: {}\n'.format(cloudtask.execution_info.exit_code))
                    if cloudtask.execution_info.exit_code != 0:
                        exit_codes_good = False
                if exit_codes_good:
                    # Get rid of job and pool so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
                    batch_client.job.delete(job_id=batch_job_name)
                    batch_client.pool.delete(pool_id=batch_job_name)  # Set up in tasks.py so that pool and job have same ID
                    cowbat_cleanup(sequencing_run_pk=sequencing_run.pk)  # This also sets task to complete
                else:
                    # Something went wrong - update status to error so user knows.
                    SequencingRun.objects.filter(pk=sequencing_run.pk).update(status='Error')
                # Delete task so we don't have to keep checking up on it.
                AzureTask.objects.filter(id=task.id).delete()

        # Also check for ParSnp
        tree_tasks = ParsnpAzureRequest.objects.filter()
        for task in tree_tasks:
            if os.path.isfile(task.exit_code_file):
                tree_task = ParsnpTree.objects.get(pk=task.tree_request.pk)
                # Read exit code. Update status to 'Error' if non-zero, 'Completed' if zero.
                # Run any tasks necessary to clean things up/have reports run.
                with open(task.exit_code_file) as f:
                    lines = f.readlines()
                for line in lines:
                    line = line.rstrip()
                    if int(line.split(',')[1]) != 0:
                        ParsnpTree.objects.filter(pk=tree_task.pk).update(status='Error')
                        ParsnpAzureRequest.objects.filter(id=task.id).delete()
                        shutil.rmtree('olc_webportalv2/media/parsnp-{}/'.format(tree_task.pk))
                    else:
                        # Upload result file to Blob storage, create download link, and clean up files.
                        # Upload entirety of reports folder for now. Maybe add visualisations of results in a bit?
                        print('Reading tree results')
                        with open('olc_webportalv2/media/parsnp-{}/parsnp.tree'.format(tree_task.pk)) as f:
                            tree_string = f.readline()
                        tree_task.newick_tree = tree_string.rstrip().replace("'", "")
                        print('Uploading result files')
                        os.remove('olc_webportalv2/media/parsnp-{}/batch_config.txt'.format(tree_task.pk))
                        os.remove('olc_webportalv2/media/parsnp-{}/exit_codes.txt'.format(tree_task.pk))
                        shutil.make_archive('olc_webportalv2/media/parsnp-{}'.format(tree_task.pk),
                                            'zip',
                                            'olc_webportalv2/media/parsnp-{}'.format(tree_task.pk))
                        blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                                       account_name=settings.AZURE_ACCOUNT_NAME)
                        tree_result_container = 'tree-{}'.format(tree_task.pk)
                        blob_client.create_container(tree_result_container)
                        blob_name = os.path.split('olc_webportalv2/media/parsnp-{}.zip'.format(tree_task.pk))[1]
                        blob_client.create_blob_from_path(container_name=tree_result_container,
                                                          blob_name=blob_name,
                                                          file_path='olc_webportalv2/media/parsnp-{}.zip'.format(tree_task.pk))
                        # Generate an SAS url with read access that users will be able to use to download their sequences.
                        print('Creating Download Link')
                        sas_token = blob_client.generate_container_shared_access_signature(container_name=tree_result_container,
                                                                                           permission=BlobPermissions.READ,
                                                                                           expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8))
                        sas_url = blob_client.make_blob_url(container_name=tree_result_container,
                                                            blob_name=blob_name,
                                                            sas_token=sas_token)
                        tree_task.download_link = sas_url
                        shutil.rmtree('olc_webportalv2/media/parsnp-{}/'.format(tree_task.pk))
                        # Delete task so we don't have to keep checking up on it.
                        ParsnpAzureRequest.objects.filter(id=task.id).delete()
                        tree_task.status = 'Complete'
                        tree_task.save()
        time.sleep(30)


class Command(BaseCommand):
    help = 'Command to monitor cowbat tasks that have been submitted to azure batch'

    def handle(self, *args, **options):
        monitor_tasks()
