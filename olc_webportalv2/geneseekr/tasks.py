import os
import glob
import shutil
import datetime
import subprocess
from Bio import SeqIO
import multiprocessing
from io import StringIO
from django.conf import settings
from olc_webportalv2.geneseekr.models import GeneSeekrRequest, GeneSeekrDetail, TopBlastHit, Tree, \
    ParsnpAzureRequest, AMRSummary, AMRAzureRequest, ProkkaRequest, ProkkaAzureRequest, NearestNeighbors, NearNeighborDetail
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.cowbat.tasks import generate_download_link
from sentry_sdk import capture_exception

from azure.storage.blob import BlockBlobService
from azure.storage.blob import BlobPermissions

import csv
from django.shortcuts import get_object_or_404

from celery import shared_task

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib


def make_config_file(seqids, job_name, input_data_folder, output_data_folder, command, config_file,
                     vm_size='Standard_D8s_v3', other_input_files=list()):
    """
    Makes a config file that can be submitted to AzureBatch via my super cool (and very poorly named)
    KubeJobSub package. Also, this assumes that you have settings imported so you have access to storage/batch names and keys
    :param seqids: List of SeqIDs that are going to be analyzed.
    :param job_name: Name of the job to be run via Batch. Also, if a zip folder has to be created,
    it will be put in olc_webportalv2/media/job_name - this will get cleaned up if it exists by our monitor_tasks function
    :param input_data_folder: Name of folder on VM that FASTA sequences will be put into.
    :param output_data_folder: Name of folder on VM that output files will be written to.
    :param command: Command that's going to be run on the SeqIDs
    :param config_file: Where you want to save the config file to.
    :param vm_size: Size of VM you want to spin up. See https://docs.microsoft.com/en-us/azure/virtual-machines/linux/sizes-general
    for a list of options.
    :param other_input_files: List of other files to put into input folder. Each entry in list should be a string
    in format container_name/file_name
    :return:
    """
    # Azure Batch does not like it one bit when too many input files get specified, so in the event that we have too
    # many (more than 50 or so), need to download them, zip them, and then upload the zip folder.
    if len(seqids) > 50:
        # Create a zip file (but put where?) of all sequences.
        job_dir = 'olc_webportalv2/media/{}'.format(job_name)
        blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                       account_name=settings.AZURE_ACCOUNT_NAME)
        for seqid in seqids:
            blob_client.get_blob_to_path(container_name='processed-data',
                                         blob_name='{}.fasta'.format(seqid),
                                         file_path=os.path.join(job_dir, '{}.fasta'.format(seqid)))
        shutil.make_archive(job_dir, 'zip', job_dir)
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
        if len(seqids) > 50:
            f.write('INPUT:={}\n'.format(job_dir + '.zip'))
            # If we have to add lots of files, prepend that to our command.
            prepend = 'unzip {zipfile} && mkdir {input_dir} && mv *.fasta {input_dir} && '.format(zipfile=job_name + '.zip',
                                                                                                  input_dir=input_data_folder)
            command = prepend + command
        else:
            if len(seqids) > 0:
                f.write('CLOUDIN:=')
                for seqid in seqids:
                    f.write('processed-data/{}.fasta '.format(seqid))
                f.write('{}\n'.format(input_data_folder))
            if len(other_input_files) > 0:
                f.write('CLOUDIN:=')
                for other_file in other_input_files:
                    f.write('{} '.format(other_file))
                f.write('{}\n'.format(input_data_folder))
        # Adding / to the end of output folder makes AzureBatch download recursively.
        if not output_data_folder.endswith('/'):
            output_data_folder += '/'
        f.write('OUTPUT:={}\n'.format(output_data_folder))
        f.write('COMMAND:={}\n'.format(command))


@shared_task
def run_prokka(prokka_request_pk):
    prokka_request = ProkkaRequest.objects.get(pk=prokka_request_pk)
    try:
        container_name = 'prokka-{}'.format(prokka_request_pk)
        run_folder = os.path.join('olc_webportalv2/media/{}'.format(container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        command = 'source $CONDA/activate /envs/prokka && mkdir {}'.format(container_name)
        # Prokka doesn't seem to have any sort of way to run on multiple genomes, so we have to run it separately
        # on each genome.
        # TODO: Make sure this still works with a really long command caused by lots of SEQIDs
        for seqid in prokka_request.seqids:
            command += ' && prokka --outdir {container_name}/{seqid} --prefix {seqid} --cpus 8 sequences/{seqid}.fasta'.format(container_name=container_name,
                                                                                                                               seqid=seqid)
        for other_file in prokka_request.other_input_files:
            command += ' && prokka --outdir {} --prefix {} --cpus 8 sequences/{}'.format(other_file,
                                                                                         os.path.split(other_file)[1].replace('.fasta', ''),
                                                                                         os.path.split(other_file)[1])
        make_config_file(seqids=prokka_request.seqids,
                         job_name=container_name,
                         input_data_folder='sequences',
                         output_data_folder=container_name,
                         command=command,
                         config_file=batch_config_file,
                         other_input_files=prokka_request.other_input_files)
        # With that done, we can submit the file to batch with our package and create a tracking object.
        subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
                        '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        ProkkaAzureRequest.objects.create(prokka_request=prokka_request,
                                          exit_code_file='NA')
        # Delete any downloaded fasta files that were used in zip creation if necessary.
        fasta_files_to_delete = glob.glob(os.path.join(run_folder, '*.fasta'))
        for fasta_file in fasta_files_to_delete:
            os.remove(fasta_file)
    except Exception as e:
        capture_exception(e)
        prokka_request.status = 'Error'
        prokka_request.save()


@shared_task
def run_sistr(sistr_request_pk):
    sistr_request = 'asdf'  # TODO: Make a sistr request object in models.
    try:
        container_name = 'sistr-{}'.format(sistr_request_pk)
        run_folder = os.path.join('olc_webportalv2/media/{}'.format(container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # TODO: This doesn't actually work right now - there's a .logfile attribute that doesn't get instantiated
         # in the command line call, so crash. Need to update OLCTools
        command = 'source $CONDA/activate /envs/cowbat && python -m spadespipeline.sistr -s sequences {container_name}' \
                  ' && mv sequences {container_name}'.format(container_name=container_name)
        make_config_file(seqids=sistr_request.seqids,
                         job_name=container_name,
                         input_data_folder='sequences',
                         output_data_folder=container_name,
                         command=command,
                         config_file=batch_config_file)
        # With that done, we can submit the file to batch with our package.
        # Use Popen to run in background so that task is considered complete.
        subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
                        '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        # TODO: Have a SISTR request object get created and tracked.
        # Also TODO: add the SISTR request to monitor_tasks in olc_webportalv2/cowbat/tasks
        # Delete any downloaded fasta files that were used in zip creation if necessary.
        fasta_files_to_delete = glob.glob(os.path.join(run_folder, '*.fasta'))
        for fasta_file in fasta_files_to_delete:
            os.remove(fasta_file)

    except:
        sistr_request.status = 'Error'
        sistr_request.save()


@shared_task
def run_amr_summary(amr_summary_pk):
    amr_summary_request = AMRSummary.objects.get(pk=amr_summary_pk)
    try:
        container_name = 'amrsummary-{}'.format(amr_summary_pk)
        run_folder = os.path.join('olc_webportalv2/media/{}'.format(container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # Delete any downloaded fasta files that were used in zip creation if necessary.
        fasta_files_to_delete = glob.glob(os.path.join(run_folder, '*.fasta'))
        for fasta_file in fasta_files_to_delete:
            os.remove(fasta_file)
        # click (which geneseekr uses) needs these env vars set or it freaks out.
        command = 'export LC_ALL=C.UTF-8 && export LANG=C.UTF-8 && ' \
                  'source $CONDA/activate /envs/cowbat && GeneSeekr blastn -s sequences -t {resfinder_db} ' \
                  '-r sequences/reports -R && python -m spadespipeline.mobrecon -s sequences -r {mob_db}' \
                  ' && mv sequences {container_name}'.format(resfinder_db='/databases/0.3.4/resfinder',
                                                             mob_db='/databases/0.3.4',
                                                             container_name=container_name)
        make_config_file(seqids=amr_summary_request.seqids,
                         job_name=container_name,
                         input_data_folder='sequences',
                         output_data_folder=container_name,
                         command=command,
                         config_file=batch_config_file,
                         other_input_files=amr_summary_request.other_input_files)
        # With that done, we can submit the file to batch with our package.
        subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
                        '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        AMRAzureRequest.objects.create(amr_request=amr_summary_request,
                                       exit_code_file='NA')
        # Delete any downloaded fasta files that were used in zip creation if necessary.
        fasta_files_to_delete = glob.glob(os.path.join(run_folder, '*.fasta'))
        for fasta_file in fasta_files_to_delete:
            os.remove(fasta_file)
    except Exception as e:
        capture_exception(e)
        amr_summary_request.status = 'Error'
        amr_summary_request.save()



@shared_task
def run_parsnp(parsnp_request_pk):
    parsnp_request = Tree.objects.get(pk=parsnp_request_pk)
    try:
        container_name = 'parsnp-{}'.format(parsnp_request_pk)
        run_folder = os.path.join('olc_webportalv2/media/{}'.format(container_name))
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        # Set number of cpus to use/VM size based on how many sequences are input.
        if len(parsnp_request.seqids) < 10:
            vm_size = 'Standard_D4s_v3'
            cpus = 4
        elif len(parsnp_request.seqids) < 30:
            vm_size = 'Standard_D8s_v3'
            cpus = 8
        elif len(parsnp_request.seqids) < 150:
            vm_size = 'Standard_D16s_v3'
            cpus = 16
        else:
            vm_size = 'Standard_D32s_v3'
            cpus = 32
        # Create our config file for submission to azure batch service.
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        make_config_file(seqids=parsnp_request.seqids,
                             job_name=container_name,
                             input_data_folder='sequences',
                             output_data_folder=container_name,
                             command='source $CONDA/activate /envs/mashtree && mkdir {outdir} && mashtree --numcpus '
                                     '{cpus} sequences/*.fasta > {outdir}/parsnp.tree'.format(outdir=container_name, cpus=cpus),
                             config_file=batch_config_file,
                             vm_size=vm_size,
                             other_input_files=parsnp_request.other_input_files)
        # With that done, we can submit the file to batch with our package.
        # Use Popen to run in background so that task is considered complete.
        subprocess.call('AzureBatch -k -d --no_clean -c {run_folder}/batch_config.txt '
                        '-o olc_webportalv2/media'.format(run_folder=run_folder), shell=True)
        ParsnpAzureRequest.objects.create(parsnp_request=parsnp_request,
                                          exit_code_file=os.path.join(run_folder, 'exit_codes.txt'))
        # Delete any downloaded fasta files that were used in zip creation if necessary.
        fasta_files_to_delete = glob.glob(os.path.join(run_folder, '*.fasta'))
        for fasta_file in fasta_files_to_delete:
            os.remove(fasta_file)
 
    except Exception as e:
        capture_exception(e)
        parsnp_request.status = 'Error'
        parsnp_request.save()


def send_email(subject, body, recipient):
    fromaddr = os.environ.get('EMAIL_HOST_USER')
    toaddr = recipient
    msg = MIMEMultipart()
    msg['From'] = fromaddr
    msg['To'] = toaddr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(user=os.environ.get('EMAIL_HOST_USER'), password=os.environ.get('EMAIL_HOST_PASSWORD'))
    text = msg.as_string()
    server.sendmail(fromaddr, toaddr, text)


# TODO: Make geneseekr run on a cloud VM - then we can move the portal VM to a much smaller machine and save money, woo!
@shared_task
def run_geneseekr(geneseekr_request_pk):
    geneseekr_request = GeneSeekrRequest.objects.get(pk=geneseekr_request_pk)
    try:
        # Step 1: Make a directory for our things.
        geneseekr_dir = 'olc_webportalv2/media/geneseekr-{}'.format(geneseekr_request_pk)
        if not os.path.isdir(geneseekr_dir):
            os.makedirs(geneseekr_dir)
        # If we're going to run things NOT via batch, all of our files to BLAST against should be stored locally.
        # Need to create links to all those files in our geneseekr_dir.
        sequence_dir = '/sequences'
        # geneseekr_sequence_dir = os.path.join(geneseekr_dir, 'sequences')
        # if not os.path.isdir(geneseekr_sequence_dir):
        #     os.makedirs(geneseekr_sequence_dir)
        # for seqid in geneseekr_request.seqids:
        #     if not os.path.exists(os.path.join(geneseekr_sequence_dir, '{}.fasta'.format(seqid))):
        #         os.symlink(src=os.path.join(sequence_dir, '{}.fasta'.format(seqid)), dst=os.path.join(geneseekr_sequence_dir, '{}.fasta'.format(seqid)))
        # With our symlinks created, also create our query file.
        geneseekr_query_dir = os.path.join(geneseekr_dir, 'targets')
        if not os.path.isdir(geneseekr_query_dir):
            os.makedirs(geneseekr_query_dir)

        with open(os.path.join(geneseekr_query_dir, 'query.tfa'), 'w') as f:
            f.write(geneseekr_request.query_sequence)

        if multiprocessing.cpu_count() > 1:
            threads_to_use = multiprocessing.cpu_count() - 1
        else:
            threads_to_use = 1
        # New way to do things: BLAST the entire database of stuff.
        cmd = 'blastn -query {query_file} -db {mega_fasta} -out {blast_report} ' \
                '-outfmt "6 qseqid sseqid pident length qlen qstart qend sstart send evalue" ' \
                '-num_alignments 50000 ' \
                '-num_threads {threads}'.format(query_file=os.path.join(geneseekr_query_dir, 'query.tfa'),
                                                mega_fasta=os.path.join(sequence_dir, 'mega_fasta.fasta'),
                                                blast_report=os.path.join(geneseekr_dir, 'blast_report.tsv'),
                                                threads=threads_to_use)

        subprocess.call(cmd, shell=True)

        print('Reading geneseekr results')
        get_blast_results(blast_result_file=os.path.join(geneseekr_dir, 'blast_report.tsv'),
                            geneseekr_task=geneseekr_request)
        get_blast_detail(blast_result_file=os.path.join(geneseekr_dir, 'blast_report.tsv'),
                            geneseekr_task=geneseekr_request)
        get_blast_top_hits(blast_result_file=os.path.join(geneseekr_dir, 'blast_report.tsv'),
                            geneseekr_task=geneseekr_request)


        # Get the blast report set up for download. Need to add a header first:
        rewrite_blast_report(blast_result_file=os.path.join(geneseekr_dir, 'blast_report.tsv'),
                             geneseekr_task=geneseekr_request)

       # Get sequence report
        geneseekr_request = get_object_or_404(GeneSeekrRequest, pk=geneseekr_request_pk)
        geneseekr_details = GeneSeekrDetail.objects.filter(geneseekr_request=geneseekr_request)
        #opens document, consolidates info from geneseekr_request and geneseekr_details dictionaries into csv
        with open(os.path.join(geneseekr_dir, 'seq_results.csv'), 'w') as seq:
            writer = csv.writer(seq)
            header = ['SeqID']
            for key, value in geneseekr_request.geneseekr_results.items():
                header.append(key)
            writer.writerow(header)
            csv_line = []
            for geneseekr_detail in geneseekr_details:
                csv_line.append(geneseekr_detail.seqid)
                for key,value in geneseekr_detail.geneseekr_results.items():
                    csv_line.append(value)
                writer.writerow(csv_line)
                csv_line = []

        print('Uploading result files')
        blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                       account_name=settings.AZURE_ACCOUNT_NAME)
        geneseekr_result_container = 'geneseekr-{}'.format(geneseekr_request.pk)
        blob_client.create_container(geneseekr_result_container)
        blob_name = os.path.split('olc_webportalv2/media/geneseekr-{}/blast_report.tsv'.format(geneseekr_request.pk))[1]
        blob_name_seq = os.path.split('olc_webportalv2/media/geneseekr-{}/seq_results.csv'.format(geneseekr_request.pk))[1]
        blob_client.create_blob_from_path(container_name=geneseekr_result_container,
                                            blob_name=blob_name,
                                            file_path='olc_webportalv2/media/geneseekr-{}/blast_report.tsv'.format(geneseekr_request.pk))
        blob_client.create_blob_from_path(container_name=geneseekr_result_container,
                                            blob_name=blob_name_seq,
                                            file_path='olc_webportalv2/media/geneseekr-{}/seq_results.csv'.format(geneseekr_request.pk))
        # Generate an SAS url with read access that users will be able to use to download their sequences.
        print('Creating Download Link')
        sas_token = blob_client.generate_container_shared_access_signature(container_name=geneseekr_result_container,
                                                                           permission=BlobPermissions.READ,
                                                                           expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8))
        sas_url = blob_client.make_blob_url(container_name=geneseekr_result_container,
                                            blob_name=blob_name,
                                            sas_token=sas_token)
        sas_url_sequence = blob_client.make_blob_url(container_name=geneseekr_result_container,
                                            blob_name=blob_name_seq,
                                            sas_token=sas_token)

        geneseekr_request.download_link = sas_url
        geneseekr_request.download_link_sequence = sas_url_sequence
        shutil.rmtree('olc_webportalv2/media/geneseekr-{}/'.format(geneseekr_request.pk))
        geneseekr_request.status = 'Complete'
        geneseekr_request.save()
        
        # email_list = geneseekr_request.emails_array
        # for email in email_list:
        #     send_email(subject='Geneseekr Query {} has finished.'.format(str(geneseekr_request)),
        #                body='This email is to inform you that the Geneseekr Query {} has completed and is available at the following link {}'.format(str(geneseekr_request),sas_url),
        #                recipient=email)    
    except Exception as e:
        capture_exception(e)
        geneseekr_request.status = 'Error'
        geneseekr_request.save()


class BlastResult:
    def __init__(self, blast_tabdelimited_line):
        # With my custom output format, headers are:
        # Index 0: query sequence name
        # Index 1: subject sequence name
        # Index 2: percent identity
        # Index 3: alignment length
        # Index 4: query sequence length
        # Index 5: query start position
        # Index 6: query end position
        # Index 7: subject start position
        # Index 8: subject end position
        # Index 9: evalue
        x = blast_tabdelimited_line.rstrip().split()
        self.query_name = x[0]
        self.subject_name = x[1]
        self.seqid = self.subject_name.split('_')[0]  # The fasta that's getting searched will always have SEQID_ as first part of contig name
        self.percent_identity = float(x[2])
        self.alignment_length = int(x[3])
        self.query_sequence_length = int(x[4])
        self.query_start_position = int(x[5])
        self.query_end_position = int(x[6])
        self.subject_start_position = int(x[7])
        self.subject_end_position = int(x[8])
        self.evalue = float(x[9])
        # Also need to have amount of query sequence covered as a percentage.
        self.query_coverage = 100.0 * self.alignment_length/self.query_sequence_length


def get_blast_results(blast_result_file, geneseekr_task):
    # This looks at all sequences we have and finds out how many of our SeqIDs have our query sequence(s).
    # First, parse the query sequence associated with the geneseekr task to find out what our query IDs are.
    query_names = list()
    for query in SeqIO.parse(StringIO(geneseekr_task.query_sequence), 'fasta'):
        query_names.append(query.id)

    # Now get a dictionary initialized where we keep track of which SeqIDs have a hit for each query gene listed.
    # When parsing the BLAST output file, if a gene is found, change the value to True
    gene_hits = dict()
    for query_name in query_names:
        gene_hits[query_name] = dict()
        for seqid in geneseekr_task.seqids:
            gene_hits[query_name][seqid] = False

    # Say anything with 90 percent identity over 90 percent of query length is a hit. # TODO: Make this user defined?
    with open(blast_result_file) as f:
        for result_line in f:
            blast_result = BlastResult(result_line)
            if blast_result.query_coverage > 90 and blast_result.percent_identity > 90 and blast_result.seqid in geneseekr_task.seqids:
                gene_hits[blast_result.query_name][blast_result.seqid] = True

    # Now for each gene, total the number of True.
    for query in gene_hits:
        num_hits = 0
        for seqid in gene_hits[query]:
            if gene_hits[query][seqid] is True:
                num_hits += 1
        percent_found = 100 * num_hits/len(geneseekr_task.seqids)
        geneseekr_task.geneseekr_results[query] = percent_found
    geneseekr_task.save()


def get_blast_detail(blast_result_file, geneseekr_task):
    # This method finds the percent ID for each gene for each SeqID
    # First, parse the query sequence associated with the geneseekr task to find out what our query IDs are.
    query_names = list()
    for query in SeqIO.parse(StringIO(geneseekr_task.query_sequence), 'fasta'):
        query_names.append(query.id)

    # Now get a dictionary initialized where we keep track of which SeqIDs have a hit for each query gene listed.
    # When parsing the BLAST output file, if a gene is found, change the value to True
    gene_hits = dict()
    for query_name in query_names:
        gene_hits[query_name] = dict()
        for seqid in geneseekr_task.seqids:
            gene_hits[query_name][seqid] = 0.0

    # Iterate through the blast file to find out the best hit
    with open(blast_result_file) as f:
        for result_line in f:
            blast_result = BlastResult(result_line)
            # Everything gets initialized to zero - only take top hit for each SeqID, which should be first hit in file.
            if blast_result.seqid in gene_hits[blast_result.query_name]:
                if gene_hits[blast_result.query_name][blast_result.seqid] == 0:
                    gene_hits[blast_result.query_name][blast_result.seqid] = blast_result.percent_identity

    for seqid in geneseekr_task.seqids:
        geneseekr_detail = GeneSeekrDetail.objects.create(geneseekr_request=geneseekr_task,
                                                          seqid=seqid)
        results = dict()
        for query in gene_hits:
            results[query] = gene_hits[query][seqid]
        geneseekr_detail.geneseekr_results = results
        geneseekr_detail.save()


def rewrite_blast_report(blast_result_file, geneseekr_task):
    # The Raw blast report that is given to users kind of sucks - this function will re-write it so that:
    # Only results that are actually part of the request get shown, and SEQIDs that were part of the request
    # but didn't have any hits get that noted.

    # First, create a copy of the blast_result_file in case anything gets real messed up.
    blast_backup_file = blast_result_file + '.bak'
    shutil.copy(blast_result_file, blast_backup_file)

    # Need to keep track of what SEQIDs have what genes. To do this, populate a dictionary where keys are SEQIDs and
    # values are a list of the target genes. Whenever we find a hit, remove the target gene from the list. Once done
    # iterating through the blast result file, write the things that are still there as missing.
    genes_not_present = dict()
    for seqid in geneseekr_task.seqids:
        genes_not_present[seqid] = list()
        for query_gene in geneseekr_task.gene_targets:
            genes_not_present[seqid].append(query_gene)

    # Now iterate through the backup file, writing to the new file as we go.
    with open(blast_backup_file) as infile:
        with open(blast_result_file, 'w') as outfile:
            outfile.write('QuerySequence\tSubjectSequence\tPercentIdentity\tAlignmentLength\tQuerySequenceLength'
                          '\tQueryStartPosition\tQueryEndPosition\tSubjectStartPosition\tSubjectEndPosition\tEValue\n')
            for result_line in infile:
                blast_result = BlastResult(result_line)
                if blast_result.seqid in geneseekr_task.seqids:
                    outfile.write(result_line)
                    try:  # Remove the gene found from the genes_not_present since we found it.
                        genes_not_present[blast_result.seqid].remove(blast_result.query_name)
                    except ValueError:
                        # Possible to get multiple hits for one gene, and then we're trying to remove something
                        # that's already gone, raising a ValueError. In that case, just don't do anything.
                        pass
            for seqid in genes_not_present:
                for query_gene in genes_not_present[seqid]:
                    outfile.write('{}\t{}\t0\t0\t0\t0\t0\t0\t0\tNA\n'.format(query_gene, seqid))


def get_blast_top_hits(blast_result_file, geneseekr_task, num_hits=50):
    # Looks at the top 50 hits for a GeneSeekr request and provides a blast-esque interface for them
    query_hit_count = dict()
    gene_targets = list()
    for query in SeqIO.parse(StringIO(geneseekr_task.query_sequence), 'fasta'):
        query_hit_count[query.id] = 0
        gene_targets.append(query.id)

    geneseekr_task.gene_targets = gene_targets
    geneseekr_task.save()
    with open(blast_result_file) as f:
        for result_line in f:
            blast_result = BlastResult(result_line)
            if blast_result.seqid in geneseekr_task.seqids:
                top_blast_hit = TopBlastHit(contig_name=blast_result.subject_name,
                                            query_coverage=blast_result.query_coverage,
                                            percent_identity=blast_result.percent_identity,
                                            start_position=blast_result.subject_start_position,
                                            end_position=blast_result.subject_end_position,
                                            e_value=blast_result.evalue,
                                            geneseekr_request=geneseekr_task,
                                            gene_name=blast_result.query_name,
                                            query_start_position=blast_result.query_start_position,
                                            query_end_position=blast_result.query_end_position,
                                            query_sequence_length=blast_result.query_sequence_length)
                top_blast_hit.save()
                query_hit_count[blast_result.query_name] += 1
            all_have_50_hits = True
            for query_id in query_hit_count:
                if query_hit_count[query_id] < num_hits:
                    all_have_50_hits = False
            if all_have_50_hits:
                break

#################### NEAREST NEIGHBORS TASK ##############################
@shared_task
def run_nearest_neighbors(nearest_neighbor_pk):
    nearest_neighbor_request = NearestNeighbors.objects.get(pk=nearest_neighbor_pk)
    try:
        work_dir = 'olc_webportalv2/media/neighbor-{}'.format(nearest_neighbor_pk)
        if not os.path.isdir(work_dir):
            os.makedirs(work_dir)
        seqids_in_metadata = list()
        for sequence_data in SequenceData.objects.filter():
            seqids_in_metadata.append(sequence_data.seqid)
        # Download requested SeqID from blob storage - we *should* have already validated that the sequence exists.
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        if nearest_neighbor_request.seqid != '':
            fasta_file = os.path.join(work_dir, nearest_neighbor_request.seqid + '.fasta')
            # TODO: Check what error happens here if blob doesn't actually exist and catch, or verify that blob exists before
            #  trying to retrieve it.
            blob_client.get_blob_to_path(container_name='processed-data',
                                         blob_name=nearest_neighbor_request.seqid + '.fasta',
                                         file_path=fasta_file)
        else:
            fasta_file = os.path.join(work_dir, nearest_neighbor_request.uploaded_file_name)
            blob_client.get_blob_to_path(container_name='neighbor-{}'.format(nearest_neighbor_request.pk),
                                         blob_name=nearest_neighbor_request.uploaded_file_name,
                                         file_path=fasta_file)

        mash_output_file = os.path.join(work_dir, 'mash_dist_results.tsv')
        cmd = '/data/web/mash-Linux64-v2.1/mash dist {query} {sketch} > {output}'.format(query=fasta_file,  # TODO: Actually install mash in dockerfile
                                                             sketch='/data/web/sketchomatic.msh',  # TODO: Change me!
                                                             output=mash_output_file)
        os.system(cmd)  # Subprocess doesn't work here. Should be OK to switch once mash is actually installed.
        shutil.make_archive(work_dir, 'zip', work_dir)
        sas_url = generate_download_link(blob_client=blob_client,
                                         container_name='neighbor-output-{}'.format(nearest_neighbor_pk),
                                         output_zipfile=work_dir + '.zip',
                                         expiry=8)
        nearest_neighbor_request.download_link = sas_url
        distances = dict()
        with open(mash_output_file) as f:
            for line in f:
                x = line.split()
                query_seqid = x[1].split('/')[-1].replace('.fasta', '')
                query_distance = float(x[2])
                # Don't show the fact we get match to self. Not useful info
                if query_seqid != nearest_neighbor_request.seqid and query_seqid in seqids_in_metadata:
                    distances[query_seqid] = query_distance
        sorted_distances = sorted(distances.items(), key=lambda kv: kv[1])
        for i in range(nearest_neighbor_request.number_neighbors):
            NearNeighborDetail.objects.create(near_neighbor_request=nearest_neighbor_request,
                                              seqid=sorted_distances[i][0],
                                              distance=sorted_distances[i][1])
        shutil.rmtree(work_dir)
        os.remove(work_dir + '.zip')
        nearest_neighbor_request.status = 'Complete'
        nearest_neighbor_request.save()
    except Exception as e:
        capture_exception(e)
        nearest_neighbor_request.status = 'Error'
        nearest_neighbor_request.save()

