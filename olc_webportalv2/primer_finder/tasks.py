# Django-related imports
from django.conf import settings
from django.shortcuts import get_object_or_404
# Standard libraries
import os
import csv
import glob
import shutil
import smtplib
import datetime
import subprocess
import multiprocessing
from io import StringIO
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
# Azure!
from azure.storage.blob import BlobPermissions
from azure.storage.blob import BlockBlobService
# Useful things!
from Bio import SeqIO, Seq
from itertools import product
from celery import shared_task
from sentry_sdk import capture_exception
# Primer-specific code
from .models import PrimerFinder, PrimerAzureRequest


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
        
# TODO: Make primerfinder run on cloud VM
@shared_task
def run_primer_finder(primer_request_pk):
    primer_request = PrimerFinder.objects.get(pk=primer_request_pk)
    if not primer_request.name:
        primer_request.name = primer_request_pk
    try:
        container_name = PrimerFinder.objects.get(pk=primer_request_pk).container_namer()
        run_folder = os.path.join('olc_webportalv2/media/{cn}'.format(cn=container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # TODO: needs to run against all hashes, use primer-hashing as guide to make hashes and maps on NAS
        make_config_file(seqids=primer_request.seqids,
                             job_name=container_name,
                             input_data_folder='sequences',
                             output_data_folder=container_name,
                            #  TODO: Needs correct input and output
                             command='source $CONDA/activate /envs/primer && mkdir {container_name} && primer -r {container_name} -s sequences/ && {rePCR} -S {outfile}.hash -r + -m {ampsize} -n {mismatches} -g 0 -G -q -o {outfile}.txt {primers}'.format(container_name=container_name,rePCR='./miniconda/envs/primer/lib/python3.6/site-packages/genemethods/assemblypipeline/ePCR/re-PCR',
                                outfile='/media/b/External/CFIA/ecoli',
                                ampsize=primer_request.ampsize,
                                mismatches=primer_request.mismatches,
                                primers=primer_request.primer_file),
                             config_file=batch_config_file,
                             vm_size=vm_size)
        # With that done, we can submit the file to batch with our package and create a tracking object.
        subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
                        '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        PrimerAzureRequest.objects.create(name=primer_request.name,
                                            exit_code_file='NA')
        primer_request.status = 'Processing'
        primer_request.save()
    except Exception as e:
        capture_exception(e)
        primer_request.status = 'Error'
        primer_request.save()

