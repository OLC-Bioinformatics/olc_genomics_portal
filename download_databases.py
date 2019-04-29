#!/usr/bin/env python

"""
This script is to be run on portal machine, from the root of the portal directory.
It will download the files necessary to make the nearest neighbors/geneseekr functionality for the database work.

It assumes you've run the make_mega_fasta and make_mash_sketch scripts locally at OLC to upload the necessary files
to blob storage.
"""

from azure.storage.blob import BlockBlobService
import os

if __name__ == '__main__':
    storage_account = input('Enter Azure Storage account name: ')
    storage_account_key = input('Enter key associated with Azure storage account: ')
    # Assume if user entered this wrong some sort of error pops up at this point.
    blob_client = BlockBlobService(account_name=storage_account,
                                   account_key=storage_account_key)

    blob_list = blob_client.list_blobs(container_name='databases')
    for blob in blob_list:
        if blob.name == 'sketchomatic.msh':
            blob_client.get_blob_to_path(container_name='databases',
                                         blob_name=blob.name,
                                         file_path=blob.name)
        elif 'mega_fasta' in blob.name:
            blob_client.get_blob_to_path(container_name='databases',
                                         blob_name=blob.name,
                                         file_path=os.path.join('/data/sequences', blob.name))
