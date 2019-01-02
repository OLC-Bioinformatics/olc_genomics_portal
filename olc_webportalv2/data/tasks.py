import datetime
import fnmatch
import shutil
import os

from background_task import background
from django.conf import settings
from olc_webportalv2.data.models import DataRequest

from azure.storage.blob import BlockBlobService
from azure.storage.blob import BlobPermissions


@background(schedule=1)
def get_assembled_data(data_request_pk):
    print('Assembled data request')
    data_request = DataRequest.objects.get(pk=data_request_pk)
    try:
        data_dir = 'olc_webportalv2/media/data_request_{}'.format(data_request_pk)
        if not os.path.isdir(data_dir):
            os.makedirs(data_dir)
        container_name = 'processed-data'
        # Setup blob client
        print('Logging into blob service')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_files = blob_client.list_blobs(container_name=container_name)
        # There's probably a better way to do this with list comprehension or something, but worry about that later
        blob_filename_list = list()
        for blob in blob_files:
            blob_filename_list.append(blob.name.replace('.fasta', ''))
        # Download necessary files to local folder, noting missing seqids.
        print('Downloading files from blob storage')
        for seqid in data_request.seqids:
            if seqid in blob_filename_list:
                blob_client.get_blob_to_path(container_name=container_name,
                                             blob_name=seqid + '.fasta',
                                             file_path=os.path.join(data_dir, seqid + '.fasta'))
            else:
                data_request.missing_seqids.append(seqid)
        # Zip the folder so it can be uploaded to blob storage.
        print('Zipping')
        shutil.make_archive(data_dir, 'zip', data_dir)
        # TODO: Have container delete itself after X amount of time - can't find native support via Azure,
        # may have to implement via cronjob or something
        # Make container to put results into and upload the zip as a blob
        print('Uploading Zip')
        data_request_container = 'data-request-{}'.format(data_request_pk)
        blob_client.create_container(data_request_container)
        blob_name = os.path.split(data_dir + '.zip')[1]
        blob_client.create_blob_from_path(container_name=data_request_container,
                                          blob_name=blob_name,
                                          file_path=data_dir + '.zip')
        # Generate an SAS url with read access that users will be able to use to download their sequences.
        print('Creating Download Link')
        sas_token = blob_client.generate_container_shared_access_signature(container_name=data_request_container,
                                                                           permission=BlobPermissions.READ,
                                                                           expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8))
        sas_url = blob_client.make_blob_url(container_name=data_request_container,
                                            blob_name=blob_name,
                                            sas_token=sas_token)
        data_request.download_link = sas_url

        # Cleanup the data request dir.
        print('Cleaning up')
        shutil.rmtree(data_dir)
        os.remove(data_dir + '.zip')
        if len(data_request.missing_seqids) == 0:
            data_request.status = 'Complete'
        else:
            data_request.status = 'Warning'
        data_request.save()
    except:
        data_request.status = 'Error'
        data_request.save()


@background(schedule=1)
def get_raw_data(data_request_pk):
    data_request = DataRequest.objects.get(pk=data_request_pk)
    try:
        print('Raw data request')
        data_request = DataRequest.objects.get(pk=data_request_pk)
        data_dir = 'olc_webportalv2/media/data_request_{}'.format(data_request_pk)
        if not os.path.isdir(data_dir):
            os.makedirs(data_dir)
        container_name = 'raw-data'
        # Setup blob client
        print('Logging into blob service')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_files = blob_client.list_blobs(container_name=container_name)
        # There's probably a better way to do this with list comprehension or something, but worry about that later
        blob_filename_list = list()
        for blob in blob_files:
            blob_filename_list.append(blob.name)
        # Download necessary files to local folder, noting missing seqids.
        print('Downloading files from blob storage')
        for seqid in data_request.seqids:
            forward_reads = fnmatch.filter(blob_filename_list, seqid + '*_R1*')
            reverse_reads = fnmatch.filter(blob_filename_list, seqid + '*_R2*')
            if len(forward_reads) == 1 and len(reverse_reads) == 1:
                blob_client.get_blob_to_path(container_name=container_name,
                                             blob_name=forward_reads[0],
                                             file_path=os.path.join(data_dir, forward_reads[0]))
                blob_client.get_blob_to_path(container_name=container_name,
                                             blob_name=reverse_reads[0],
                                             file_path=os.path.join(data_dir, reverse_reads[0]))
            else:
                data_request.missing_seqids.append(seqid)
        # Zip the folder so it can be uploaded to blob storage.
        print('Zipping')
        shutil.make_archive(data_dir, 'zip', data_dir)
        # TODO: Have container delete itself after X amount of time - can't find native support via Azure,
        # may have to implement via cronjob or something
        # Make container to put results into and upload the zip as a blob
        print('Uploading Zip')
        data_request_container = 'data-request-{}'.format(data_request_pk)
        blob_client.create_container(data_request_container)
        blob_name = os.path.split(data_dir + '.zip')[1]
        blob_client.create_blob_from_path(container_name=data_request_container,
                                          blob_name=blob_name,
                                          file_path=data_dir + '.zip')
        # Generate an SAS url with read access that users will be able to use to download their sequences.
        print('Creating Download Link')
        sas_token = blob_client.generate_container_shared_access_signature(container_name=data_request_container,
                                                                           permission=BlobPermissions.READ,
                                                                           expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8))
        sas_url = blob_client.make_blob_url(container_name=data_request_container,
                                            blob_name=blob_name,
                                            sas_token=sas_token)
        data_request.download_link = sas_url

        # Cleanup the data request dir.
        print('Cleaning up')
        shutil.rmtree(data_dir)
        os.remove(data_dir + '.zip')
        if len(data_request.missing_seqids) == 0:
            data_request.status = 'Complete'
        else:
            data_request.status = 'Warning'
        data_request.save()
    except:
        data_request.status = 'Error'
        data_request.save()
