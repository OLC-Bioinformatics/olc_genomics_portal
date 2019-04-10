#!/usr/bin/env python

import glob
from Bio import SeqIO
import os

if __name__ == '__main__':
    local_fastas = glob.glob('/mnt/nas2/processed_sequence_data/miseq_assemblies/*/BestAssemblies/*.fasta')
    local_fastas += glob.glob('/mnt/nas2/processed_sequence_data/merged_assemblies/*/BestAssemblies/*.fasta')
    local_fastas += glob.glob('/mnt/nas2/processed_sequence_data/nextseq_assemblies/*/BestAssemblies/*.fasta')

    done_count = 0
    for fasta in local_fastas:
        sequences = list()
        for contig in SeqIO.parse(fasta, 'fasta'):
            contig.id = os.path.split(fasta)[1].replace('.fasta', '') + '_' + contig.id
            sequences.append(contig)
        with open('mega_fasta.fasta', 'a+') as f:
            SeqIO.write(sequences, f, 'fasta')
        done_count += 1
        print('Done {}/{}'.format(done_count, len(local_fastas)), end='\r')

    cmd = 'makeblastdb -dbtype nucl -in mega_fasta.fasta'
    os.system(cmd)
