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
            # Check if all tasks (within batch, this terminology gets confusing) associated
            # with this job have completed.
            tasks_completed = True
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False

            # Assuming that things have completed, check exit codes. Set status to error if any are non-zero.
            if tasks_completed:
                exit_codes_good = True
                for cloudtask in batch_client.task.list(batch_job_name):
                    if cloudtask.execution_info.exit_code != 0:
                        exit_codes_good = False
                batch_client.job.delete(job_id=batch_job_name)
                batch_client.pool.delete(pool_id=batch_job_name)  # Set up in tasks.py so that pool and job have same ID
                if exit_codes_good:
                    # Get rid of job and pool so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
                    cowbat_cleanup(sequencing_run_pk=sequencing_run.pk)  # This also sets task to complete
                else:
                    # Something went wrong - update status to error so user knows.
                    SequencingRun.objects.filter(pk=sequencing_run.pk).update(status='Error')
                # Delete task so we don't have to keep checking up on it.
                AzureTask.objects.filter(id=task.id).delete()

        # Also check for Parsnp tree creation tasks
        tree_tasks = ParsnpAzureRequest.objects.filter()
        for task in tree_tasks:
            tree_task = ParsnpTree.objects.get(pk=task.tree_request.pk)
            batch_job_name = 'parsnp-{}'.format(task.tree_request.pk)
            # Check if tasks related with this parsnp job have finished.
            tasks_completed = True
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False

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
                    with open('olc_webportalv2/media/parsnp-{}/parsnp.tree'.format(tree_task.pk)) as f:
                        tree_string = f.readline()
                    tree_task.newick_tree = tree_string.rstrip().replace("'", "")
                    blob_client.delete_container(container_name=batch_job_name)
                    # Should now have results from parsnp in olc_webportalv2/media/parsnp-X, where X is pk of parsnp request
                    parsnp_output_folder = os.path.join('olc_webportalv2/media', batch_job_name)
                    os.remove(os.path.join(parsnp_output_folder, 'batch_config.txt'))
                    # Need to zip this folder and then upload the zipped folder to cloud
                    shutil.make_archive(parsnp_output_folder, 'zip', parsnp_output_folder)
                    tree_result_container = 'tree-{}'.format(tree_task.pk)
                    blob_client.create_container(tree_result_container)
                    blob_name = os.path.split(parsnp_output_folder + '.zip')[1]
                    blob_client.create_blob_from_path(container_name=tree_result_container,
                                                      blob_name=blob_name,
                                                      file_path=parsnp_output_folder + '.zip')
                    # Generate an SAS url with read access that users will be able to use to download their sequences.
                    sas_token = blob_client.generate_container_shared_access_signature(container_name=tree_result_container,
                                                                                       permission=BlobPermissions.READ,
                                                                                       expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8))
                    sas_url = blob_client.make_blob_url(container_name=tree_result_container,
                                                        blob_name=blob_name,
                                                        sas_token=sas_token)
                    shutil.rmtree(parsnp_output_folder)
                    tree_task.download_link = sas_url
                    tree_task.status = 'Complete'
                    tree_task.save()

                else:
                    ParsnpTree.objects.filter(pk=task.tree_request.pk).update(status='Error')
                # Delete task so we don't keep iterating over it.
                ParsnpAzureRequest.objects.filter(id=task.id).delete()
        time.sleep(30)


def download_container(blob_service, container_name, output_dir):
    # Modified from https://blogs.msdn.microsoft.com/brijrajsingh/2017/05/27/downloading-a-azure-blob-storage-container-python/
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


class Command(BaseCommand):
    help = 'Command to monitor cowbat tasks that have been submitted to azure batch'

    def handle(self, *args, **options):
        monitor_tasks()
