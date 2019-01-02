#!/usr/bin/env python

from azure.storage.blob import BlockBlobService
import glob
import os

if __name__ == '__main__':
    account_name = input('Enter Azure Storage account name: ')
    account_key = input('Enter Azure Storage account key: ')

    blob_service = BlockBlobService(account_name=account_name,
                                    account_key=account_key)

    fastas_in_cloud = list()
    # Get a list of all the files we already have in processed-data container
    generator = blob_service.list_blobs(container_name='processed-data')
    for blob in generator:
        fastas_in_cloud.append(blob.name)

    local_fastas = glob.glob('/mnt/nas2/processed_sequence_data/miseq_assemblies/*/BestAssemblies/*.fasta')
    for fasta in local_fastas:
        fasta_name = os.path.basename(fasta)
        if fasta_name not in fastas_in_cloud:
            print('Uploading {}'.format(fasta_name))
            blob_service.create_blob_from_path(container_name='processed-data',
                                               blob_name=fasta_name,
                                               file_path=fasta)
    # Do same for merged assemblies
    local_fastas = glob.glob('/mnt/nas2/processed_sequence_data/merged_assemblies/*/BestAssemblies/*.fasta')
    for fasta in local_fastas:
        fasta_name = os.path.basename(fasta)
        if fasta_name not in fastas_in_cloud:
            print('Uploading {}'.format(fasta_name))
            blob_service.create_blob_from_path(container_name='processed-data',
                                               blob_name=fasta_name,
                                               file_path=fasta)
