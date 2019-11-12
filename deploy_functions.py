#!/usr/bin/env python3
from azure.storage.blob import BlockBlobService
from argparse import ArgumentParser
from subprocess import call
import multiprocessing
from glob import glob
from Bio import SeqIO
import csv
import os
__author__ = 'adamkoziol'


class DeployPreparation(object):

    def main(self):
        self.assembly_find()
        self.make_metadata_csv()
        self.metadata_assembly_congruence()

    def assembly_find(self):
        total = 0
        for assembly_path in self.assembly_paths:
            assemblies = glob(assembly_path)
            print('assembly_path:', assembly_path, len(assemblies))
            total += len(assemblies)
            for assembly in assemblies:
                self.local_fastas.append(assembly)
                self.fasta_seqids.append(os.path.splitext(os.path.basename(assembly))[0])
        print('local', len(self.local_fastas), self.local_fastas[0], self.local_fastas[-1])
        print('fasta seqids', len(self.fasta_seqids), self.fasta_seqids[0], self.fasta_seqids[-1])
        print('total', total)

    def make_metadata_csv(self):
        with open('SeqTracking.csv', encoding='ISO-8859-1') as csvfile:
            with open('Metadata_csv.csv', 'w') as outfile:
                outfile.write('SeqID,Genus,Quality\n')
                reader = csv.DictReader(csvfile)
                for row in reader:
                    if row['Genus'] == '':
                        genus = 'NA'
                    else:
                        genus = row['Genus']
                    seqid = row['SEQID'].rstrip()
                    self.metadata_seqids.append(seqid)
                    if row['CuratorFlag'] == 'REFERENCE':
                        quality = 'Reference'
                    elif row['CuratorFlag'] == 'PASS':
                        quality = 'Pass'
                    else:
                        quality = 'Fail'
                    outfile.write('{},{},{}\n'.format(seqid, genus, quality))
        print('metadata seqids', len(self.metadata_seqids), self.metadata_seqids[0], self.metadata_seqids[-1])

    def metadata_assembly_congruence(self):
        missing_metadata = set(self.fasta_seqids) - set(self.metadata_seqids)
        missing_fastas = set(self.metadata_seqids) - set(self.fasta_seqids)

        print('Missing metadata for: {missing_metadata}'
              .format(missing_metadata=', '.join(sorted(list(missing_metadata)))))
        # print('Missing FASTAs for {missing_fastas}'
        #       .format(missing_fastas=', '.join(sorted(list(missing_fastas)))))

    def make_mash_sketch(self):
        cmd = 'mash sketch {fasta} -p {threads} -o sketchomatic'.format(fasta=' '.join(self.assembly_paths),
                                                                        threads=self.cpus)
        print(cmd)
        # call(cmd, shell=True)

        # Assume that databases container already exists.
        self.blob_client.create_blob_from_path(container_name='databases',
                                               blob_name='sketchomatic.msh',
                                               file_path='sketchomatic.msh')

    def make_mega_fasta(self):

        done_count = 0
        for fasta in self.local_fastas:
            sequences = list()
            for contig in SeqIO.parse(fasta, 'fasta'):
                contig.id = os.path.split(fasta)[1].replace('.fasta', '') + '_' + contig.id
                sequences.append(contig)
            with open('mega_fasta.fasta', 'a+') as f:
                SeqIO.write(sequences, f, 'fasta')
            done_count += 1
            print('Done {}/{}'.format(done_count, len(self.local_fastas)), end='\r')

        cmd = 'makeblastdb -dbtype nucl -in mega_fasta.fasta'
        print(cmd)
        # call(cmd, shell=True)

        # Now upload all the mega_fasta things to blob_storage.
        # databases container must already be created.
        things_to_upload = glob('mega_fasta*.nhr')
        things_to_upload += glob('mega_fasta*.nin')
        things_to_upload += glob('mega_fasta*.nsq')
        things_to_upload += glob('mega_fasta*.nal')
        for thing_to_upload in things_to_upload:
            self.blob_client.create_blob_from_path(container_name='databases',
                                                   blob_name=thing_to_upload,
                                                   file_path=thing_to_upload)

    def upload_sequence_data_to_cloud(self):
        pass

    def __init__(self, sequencepath):
        # storage_account = input('Enter Azure Storage account name: ')
        # storage_account_key = input('Enter key associated with Azure storage account: ')
        # # Assume if user entered this wrong some sort of error pops up at this point.
        # self.blob_client = BlockBlobService(account_name=storage_account,
        #                                     account_key=storage_account_key)
        if sequencepath.startswith('~'):
            self.sequencepath = os.path.abspath(os.path.expanduser(os.path.join(sequencepath)))
        else:
            self.sequencepath = os.path.abspath(os.path.join(sequencepath))
        self.cpus = multiprocessing.cpu_count() - 1
        self.assembly_types = [
            'miseq_assemblies',
            'merged_assemblies',
            'nextseq_assemblies',
            'hybrid_assemblies',
            'nanopore_assemblies',
            'pacbio_assemblies'
        ]
        self.assembly_paths = list()
        for assembly_type in self.assembly_types:
            self.assembly_paths.append(os.path.join(self.sequencepath, assembly_type, '*', 'BestAssemblies', '*.fasta'))
        self.local_fastas = list()
        self.fasta_seqids = list()
        self.metadata_seqids = list()


def cli():
    # Parser for arguments
    parser = ArgumentParser(description='Assemble genomes from Illumina fastq files')
    parser.add_argument('-s', '--sequencepath',
                        default='/mnt/nas2/processed_sequence_data/',
                        help='Path to folder containing assemblies. Default is /mnt/nas2/processed_sequence_data/')
    # Get the arguments into an object
    arguments = parser.parse_args()
    deploy = DeployPreparation(sequencepath=arguments.sequencepath)
    deploy.main()


if __name__ == '__main__':
    cli()
