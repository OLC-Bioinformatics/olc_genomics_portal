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


def monitor_tasks():
    while True:
        # Check for completed cowbat runs
        azure_tasks = AzureTask.objects.filter()
        for task in azure_tasks:
            if os.path.isfile(task.exit_code_file):
                sequencing_run = SequencingRun.objects.get(pk=task.sequencing_run.pk)
                # Read exit code. Update status to 'Error' if non-zero, 'Completed' if zero.
                # Run any tasks necessary to clean things up/have reports run.
                with open(task.exit_code_file) as f:
                    lines = f.readlines()
                for line in lines:
                    line = line.rstrip()
                    if int(line.split(',')[1]) != 0:
                        SequencingRun.objects.filter(pk=sequencing_run.pk).update(status='Error')
                        AzureTask.objects.filter(id=task.id).delete()
                        """
                        send_mail(subject='Assembly Error - Run {} was successfully submitted Azure Batch, but did not complete assembly'.format(str(sequencing_run)),
                                  message='Fix it!',
                                  from_email=settings.EMAIL_HOST_USER,
                                  recipient_list=['andrew.low@canada.ca'])
                        """
                    else:
                        cowbat_cleanup(sequencing_run_pk=sequencing_run.pk)  # This also sets task to complete
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
