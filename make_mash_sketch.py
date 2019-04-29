#!/usr/bin/env python

import os
from azure.storage.blob import BlockBlobService

if __name__ == '__main__':
    storage_account = input('Enter Azure Storage account name: ')
    storage_account_key = input('Enter key associated with Azure storage account: ')
    # Assume if user entered this wrong some sort of error pops up at this point.
    blob_client = BlockBlobService(account_name=storage_account,
                                   account_key=storage_account_key)
    miseq_assemblies = '/mnt/nas2/processed_sequence_data/miseq_assemblies/*/BestAssemblies/*.fasta'
    merged_assemblies = '/mnt/nas2/processed_sequence_data/merged_assemblies/*/BestAssemblies/*.fasta'
    nextseq_assemblies = '/mnt/nas2/processed_sequence_data/nextseq_assemblies/*/BestAssemblies/*.fasta'

    cmd = 'mash sketch {} {} {} -p 4 -o sketchomatic'.format(miseq_assemblies, merged_assemblies, nextseq_assemblies)
    os.system(cmd)

    # Assume that databases container already exists.
    blob_client.create_blob_from_path(container_name='databases',
                                      blob_name='sketchomatic.msh',
                                      file_path='sketchomatic.msh')
