#!/usr/bin/env python

import glob
from Bio import SeqIO
import os
from azure.storage.blob import BlockBlobService

if __name__ == '__main__':

    storage_account = input('Enter Azure Storage account name: ')
    storage_account_key = input('Enter key associated with Azure storage account: ')

    # local_fastas = glob.glob('/mnt/nas2/processed_sequence_data/miseq_assemblies/*/BestAssemblies/*.fasta')
    # local_fastas += glob.glob('/mnt/nas2/processed_sequence_data/merged_assemblies/*/BestAssemblies/*.fasta')
    # local_fastas += glob.glob('/mnt/nas2/processed_sequence_data/nextseq_assemblies/*/BestAssemblies/*.fasta')

    # Assume if user entered this wrong some sort of error pops up at this point.
    blob_client = BlockBlobService(account_name=storage_account,
                                   account_key=storage_account_key)
    # done_count = 0
    # for fasta in local_fastas:
    #     sequences = list()
    #     for contig in SeqIO.parse(fasta, 'fasta'):
    #         contig.id = os.path.split(fasta)[1].replace('.fasta', '') + '_' + contig.id
    #         sequences.append(contig)
    #     with open('mega_fasta.fasta', 'a+') as f:
    #         SeqIO.write(sequences, f, 'fasta')
    #     done_count += 1
    #     print('Done {}/{}'.format(done_count, len(local_fastas)), end='\r')

    cmd = 'makeblastdb -dbtype nucl -in mega_fasta.fasta'
    os.system(cmd)

    # Now upload all the mega_fasta things to blob_storage.
    # databases container must already be created.
    things_to_upload = glob.glob('mega_fasta*.nhr')
    things_to_upload += glob.glob('mega_fasta*.nin')
    things_to_upload += glob.glob('mega_fasta*.nsq')
    things_to_upload += glob.glob('mega_fasta*.nal')
    for thing_to_upload in things_to_upload:
        blob_client.create_blob_from_path(container_name='databases',
                                          blob_name=thing_to_upload,
                                          file_path=thing_to_upload)
