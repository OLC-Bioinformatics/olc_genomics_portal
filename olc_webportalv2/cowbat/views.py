# Django-related imports
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
# Standard libraries
import json
import logging
import fnmatch
import os
import re
# Portal-specific things
from olc_webportalv2.cowbat.models import SequencingRun, DataFile, ResearchRun, SummaryMetadata
from olc_webportalv2.cowbat.forms import RunNameForm, RealTimeForm, RunRequestForm, CustomRunForm
from olc_webportalv2.cowbat.tasks import escape_ansi, run_cowbat_batch
from olc_webportalv2.filezone.methods import calculate_checksum
from olc_webportalv2.geneseekr.forms import EmailForm
# Azure!
from azure.storage.blob import BlockBlobService
import azure.batch.batch_service_client as batch
import azure.batch.batch_auth as batch_auth
import azure.batch.models as batchmodels
# Task Management
from kombu import Queue
# Autocomplete
from dal import autocomplete
from django.db.models import Q

log = logging.getLogger(__name__)


def find_percent_complete(sequencing_run):
    try:
        job_id = str(sequencing_run).lower().replace('_', '-')
        credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
        batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
        node_files = batch_client.file.list_from_task(job_id=job_id, task_id=job_id, recursive=True)
        final_num_reports = 26
        current_subfolders = 0
        for node_file in node_files:
            if 'reports' in node_file.name:
                current_subfolders += 1
        if final_num_reports == 0:
            percent_completed = 1
        else:
            percent_completed = int(100.0 * (current_subfolders / final_num_reports))

    except batchmodels.BatchErrorException:  # Means task and job have not yet been created
        percent_completed = 1
    return percent_completed


def find_percentage_complete(sequencing_run):
    job_id = sequencing_run.job_id
    credentials = batch_auth.SharedKeyCredentials(
        settings.BATCH_ACCOUNT_NAME,
        settings.BATCH_ACCOUNT_KEY
    )
    batch_client = batch.BatchServiceClient(
        credentials,
        base_url=settings.BATCH_ACCOUNT_URL
    )
    node_files = batch_client.file.list_from_task(
        job_id=job_id,
        task_id=sequencing_run.task_id,
        recursive=True
    )

    # Initialise a dictionary to store the stderr file information
    contents = {}
    try:
        for node_file in node_files:
            # Stderr.txt file
            if 'stderr' in node_file.name:
                try:
                    contents[node_file.name] = \
                        batch_client.file.get_from_task(
                            job_id=sequencing_run.job_id,
                            task_id=sequencing_run.task_id,
                            file_path=node_file.name
                        )
                except Exception:
                    pass
    except batchmodels.BatchErrorException:
        # The run hasn't started assembling yet
        return

    # Define a variable to store the final line
    final_line = str()

    # Extract the final lne from the log
    for _, content_object in contents.items():
        for content_chunk in content_object:
            try:
                clean_line = escape_ansi(line=content_chunk.decode())
                final_line = clean_line.split('\n')[-2]
            except Exception:
                pass
    # Print the final line
    print(final_line)
    
    # Define a pattern for the date, time, and message
    pattern = r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)"

    # Search for the pattern in the line
    match = re.search(pattern, final_line)

    # Initalise the message variable
    message = ''

    # If a match is found
    if match:
        # Extract the date, time, and message
        _, __, message = match.groups()

    if not message:
        return
    
    # Print the message
    print(message)

    # Define the path to the JSON file
    json_file_path = \
        "olc_webportalv2/cowbat/cowbat_percent_complete.json"

    # Open the JSON file and load the data
    with open(json_file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)

    # Iterate over the data
    for entry in data:
        # If the message matches
        if entry['message'] == message:
            # Print the message and percent complete
            print(entry['message'], entry['percent_complete'])
            # Update the model
            sequencing_run.percent_complete = entry['percent_complete']
            sequencing_run.save()


def check_uploaded_seqids(sequencing_run):
    container_name = str(sequencing_run).lower().replace('_', '-')
    blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                   account_name=settings.AZURE_ACCOUNT_NAME)
    blob_filenames = list()
    blobs = blob_client.list_blobs(container_name=container_name)
    for blob in blobs:
        blob_filenames.append(blob.name)
    # uploaded_seqids = list()
    for seqid in sequencing_run.seqids:
        forward_reads = fnmatch.filter(blob_filenames, seqid + '*_R1*')
        reverse_reads = fnmatch.filter(blob_filenames, seqid + '*_R2*')
        if len(forward_reads) == 1:
            if seqid not in sequencing_run.uploaded_forward_reads:
                sequencing_run.uploaded_forward_reads.append(seqid)
        else:
            if seqid not in sequencing_run.forward_reads_to_upload:
                sequencing_run.forward_reads_to_upload.append(seqid)
        if len(reverse_reads) == 1:
            if seqid not in sequencing_run.uploaded_reverse_reads:
                sequencing_run.uploaded_reverse_reads.append(seqid)
        else:
            if seqid not in sequencing_run.reverse_reads_to_upload:
                sequencing_run.reverse_reads_to_upload.append(seqid)
        # if len(forward_reads) == 1 and len(reverse_reads) == 1:
        #     sequencing_run.uploaded_seqids.append(seqid)
        # else:
        #     seqids_to_upload.append(seqid)
        # for seqid in seqids_to_upload:
        #     if seqid not in sequencing_run.seqids_to_upload:
        #         sequencing_run.seqids_to_upload.append(seqid)
        # for seqid in uploaded_seqids:
        #     if seqid not in sequencing_run.uploaded_seqids:
        #         sequencing_run.uploaded_seqids.append(seqid)
        sequencing_run.save()


# Create your views here.
@login_required
def cowbat_processing(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    summary_results = SummaryMetadata.objects.filter(sequencing_run_id=sequencing_run_pk)
    if sequencing_run.status == 'Unprocessed':
        SequencingRun.objects.filter(pk=sequencing_run.pk).update(status='Processing')
        run_cowbat_batch.apply_async(queue='cowbat', args=(sequencing_run.pk,))

    # Find percent complete (approximately). Not sure that having calls to azure batch API in views is a good thing.
    # Will have to see if performance is terrible because of it.
    if sequencing_run.status == 'Processing':
        find_percentage_complete(sequencing_run)

    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            Email = form.cleaned_data.get('email')
            if Email not in sequencing_run.emails_array:
                sequencing_run.emails_array.append(Email)
                sequencing_run.save()
                form = EmailForm()
                messages.success(request, _('Email saved'))

    return render(request,
                  'cowbat/cowbat_processing.html',
                  {
                      'sequencing_run': sequencing_run,
                      'form': form,
                      'progress': sequencing_run.percent_complete,
                      'summary_results': summary_results
                  })


@login_required
def assembly_home(request):
    sequencing_runs = SequencingRun.objects.order_by('-run_name')
    return render(request,
                  'cowbat/assembly_home.html',
                  {
                      'sequencing_runs': sequencing_runs
                  })


@login_required
def upload_metadata(request):
    form = RunNameForm()
    if request.method == 'POST':
        form = RunNameForm(request.POST)
        if form.is_valid():
            if not SequencingRun.objects.filter(run_name=form.cleaned_data.get('run_name')).exists():
                sequencing_run, created = SequencingRun.objects \
                    .update_or_create(run_name=form.cleaned_data.get('run_name'),
                                      seqids=list())
            else:
                sequencing_run = SequencingRun.objects.get(run_name=form.cleaned_data.get('run_name'))
            files = [request.FILES.get('file[%d]' % i) for i in range(0, len(request.FILES))]
            container_name = sequencing_run.run_name.lower().replace('_', '-')
            blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                           account_key=settings.AZURE_ACCOUNT_KEY)
            blob_client.create_container(container_name)
            for item in files:
                # Calculate the checksum
                blob_headers = calculate_checksum(item=item)
                # Upload to blob storage
                blob_client.create_blob_from_bytes(
                    container_name=container_name,
                    blob_name=item.name,
                    blob=item.read(),
                    content_settings=blob_headers
                )
                if item.name == 'SampleSheet.csv':
                    instance = DataFile(sequencing_run=sequencing_run, data_file=item)
                    instance.save()
                    with open('olc_webportalv2/media/{run_name}/SampleSheet.csv'
                              .format(run_name=str(sequencing_run))) as f:
                        lines = f.readlines()
                    seqid_start = False
                    seqid_list = list()
                    realtime_dict = dict()
                    # Sample plate column in SampleSheet should have Lab/Whatever other ID.
                    # Store that data in a dictionary with SeqIDs as keys and LabIDs as values
                    sample_plate_dict = dict()
                    for i in range(len(lines)):
                        if seqid_start:
                            seqid = lines[i].split(',')[0]
                            labid = lines[i].split(',')[2]
                            sample_plate_dict[seqid] = labid
                            try:
                                realtime = lines[i].rstrip().split(',')[9]
                            except IndexError:
                                realtime = ''

                            seqid_list.append(seqid)
                            if realtime == 'TRUE' or realtime == 'VRAI':
                                realtime_dict[seqid] = 'True'  # Not sure JSONField this gets stored in can handle bool
                            else:
                                realtime_dict[seqid] = 'False'
                        if 'Sample_ID' in lines[i]:
                            seqid_start = True
                    SequencingRun.objects.filter(pk=sequencing_run.pk).update(seqids=seqid_list,
                                                                              realtime_strains=realtime_dict,
                                                                              sample_plate=sample_plate_dict)
            # TODO: Change this back to verify_realtime once we've gotten the OK from external labs to make them
            #  validate their data.
            # return redirect('cowbat:verify_realtime', sequencing_run_pk=sequencing_run.pk)
            return redirect('cowbat:upload_interop', sequencing_run_pk=sequencing_run.pk)
    return render(request,
                  'cowbat/upload_metadata.html',
                  {
                      'form': form
                  })


@login_required
def verify_realtime(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    form = RealTimeForm(instance=sequencing_run)
    if request.method == 'POST':
        form = RealTimeForm(request.POST, instance=sequencing_run)
        if form.is_valid():
            # Read form data, update realtime strains as necessary.
            seqids = form.cleaned_data.get('realtime_select')
            for seqid in sequencing_run.realtime_strains:
                if seqid in seqids:
                    sequencing_run.realtime_strains[seqid] = 'True'
                else:
                    sequencing_run.realtime_strains[seqid] = 'False'
                strain_name = form.cleaned_data.get(seqid)
                sequencing_run.sample_plate[seqid] = strain_name
            sequencing_run.save()
            # Also modify samplesheet to reflect the updated Realtime strains and overwrite the previous upload
            # to blob storage
            samplesheet_path = 'olc_webportalv2/media/{run_name}/SampleSheet.csv'.format(run_name=str(sequencing_run))
            with open(samplesheet_path) as f:
                lines = f.readlines()
            seqid_start = False
            with open(samplesheet_path, 'w') as f:
                for i in range(len(lines)):
                    if seqid_start:
                        seqid = lines[i].split(',')[0]
                        line_split = lines[i].split(',')
                        line_split[2] = sequencing_run.sample_plate[seqid]
                        if sequencing_run.realtime_strains[seqid] == 'True':
                            line_split[-1] = 'TRUE\n'
                        else:
                            line_split[-1] = '\n'
                        to_write = ','.join(line_split)
                        f.write(to_write)
                    else:
                        f.write(lines[i])
                    if 'Sample_ID' in lines[i]:
                        seqid_start = True
            container_name = sequencing_run.run_name.lower().replace('_', '-')
            blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                           account_key=settings.AZURE_ACCOUNT_KEY)
            blob_client.create_blob_from_path(container_name=container_name,
                                              blob_name='SampleSheet.csv',
                                              file_path=samplesheet_path)
            return redirect('cowbat:upload_interop', sequencing_run_pk=sequencing_run.pk)
    return render(request,
                  'cowbat/verify_realtime.html',
                  {
                      'form': form,
                      'sequencing_run': sequencing_run
                  })


@login_required
def upload_interop(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    if request.method == 'POST':
        container_name = sequencing_run.run_name.lower().replace('_', '-')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_client.create_container(container_name)
        files = [request.FILES.get('file[%d]' % i) for i in range(0, len(request.FILES))]
        for item in files:
            # Calculate the checksum
            blob_headers = calculate_checksum(item=item)
            # Upload to blob storage
            blob_client.create_blob_from_bytes(
                container_name=container_name,
                blob_name=os.path.join('InterOp', item.name),
                blob=item.read(),
                content_settings=blob_headers
            )
        return redirect('cowbat:upload_sequence_data', sequencing_run_pk=sequencing_run.pk)
    return render(request,
                  'cowbat/upload_interop.html',
                  {
                      'sequencing_run': sequencing_run
                  })


@login_required
def upload_sequence_data(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    check_uploaded_seqids(sequencing_run=sequencing_run)
    seqid_list = list()
    if request.method == 'POST':
        check_uploaded_seqids(sequencing_run=sequencing_run)
        container_name = sequencing_run.run_name.lower().replace('_', '-')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_client.create_container(container_name)
        for i in range(0, len(request.FILES)):
            item = request.FILES.get('file[%d]' % i)
            # Calculate the checksum
            blob_headers = calculate_checksum(item=item)
            # Upload to blob storage
            blob_client.create_blob_from_bytes(
                container_name=container_name,
                blob_name=item.name,
                blob=item.read(),
                content_settings=blob_headers
            )

        # return redirect('cowbat:cowbat_processing', sequencing_run_pk=sequencing_run.pk)
    return render(request,
                  'cowbat/upload_sequence_data.html',
                  {
                      'sequencing_run': sequencing_run,
                  })


@login_required
def retry_sequence_data_upload(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    sequencing_run.status = 'Unprocessed'
    sequencing_run.save()
    return redirect('cowbat:upload_sequence_data', sequencing_run_pk=sequencing_run.pk)


def find_research_runs():
    blob_client = BlockBlobService(
        account_name=settings.AZURE_ACCOUNT_NAME,
        account_key=settings.AZURE_ACCOUNT_KEY
    )
    containers = blob_client.list_containers()
    # Initialise a set to store all the unique run names
    run_set = set()
    for container in containers:
        if re.match('\d{5,6}-[a-z0-9]', container.name) and '-output' not in container.name:
            run_set.add(container.name)
    return sorted(list(run_set))


class RunAutoCompleter(autocomplete.Select2ListView):

    def __init__(self, **kwargs):
        self.category = 'run'
        super().__init__(**kwargs)

    def get_list(self):
        qs = ResearchRun.objects.all()
        if self.q:
            qs.filter(run_name__icontains=self.q)
        #
        return sorted(list(set(str(result.run_name) for result in qs)))


@login_required
def research_assembly(request):
    # Clear out all the previous data
    ResearchRun.objects.all().delete()
    form = RunRequestForm()
    run_list = find_research_runs()
    for run in run_list:
        ResearchRun.objects.get_or_create(run_name=run)
    custom_form = CustomRunForm()
    if request.method == 'POST':
        custom_form = CustomRunForm(request.POST)
        # form = RunRequestForm(request.POST)
        if custom_form.is_valid():
            sequencing_run = custom_form.save(commit=False)
            sequencing_run.basic_assembly = custom_form.cleaned_data.get('basic_assembly')
            sequencing_run.preprocess = custom_form.cleaned_data.get('preprocess')
            sequencing_run.run_name = custom_form.cleaned_data.get('run_name')
            sequencing_run.nextseq = custom_form.cleaned_data.get('nextseq')
            sequencing_run.container = str()
            sequencing_run.seqids = list()
            sequencing_run.save()
            return redirect('cowbat:cowbat_processing', sequencing_run_pk=sequencing_run.pk)

    return render(request,
                  'cowbat/research_assembly.html',
                  {
                      'form': form,
                      'custom_form': custom_form
                  })


@login_required
def assembly_results(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    # Extract the summary metadata object using the sequencing run pk
    summary_results = get_object_or_404(SummaryMetadata, sequencing_run_id=sequencing_run_pk)
    headers = ['SeqID', 'SampleName', 'Genus', 'E_coli_Serotype', 'SISTR_serovar', 'GeneSeekr_Profile',
               'Vtyper_Profile', ' rMLST_Result', ' MLST_Result', 'N50', 'NumContigs', 'TotalLength',
               'AverageCoverageDepth', 'ConfindrContamSNVs', 'SequencingDate', 'Analyst', 'Flowcell',
               'MachineName', 'AssemblyDate', 'PipelineVersion', 'Database']

    # Convert keys to integers and sort the dictionary
    sorted_results = {
        int(k): v for k, v in sorted(
            summary_results.summary_results.items(),
            key=lambda item: int(item[0]),
            reverse=True
        )
    }
    return render(request,
                  'cowbat/assembly_results.html',
                  {
                      'headers': headers,
                      'summary_results': sorted_results,
                      'sequencing_run': sequencing_run,
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def research_assembly_home(request):
    return render(request,
                  'cowbat/research_assembly_home.html',
                  {}
                  )


@csrf_exempt  # needed or IE explodes
@login_required
def custom_run_request(request):
    custom_form = CustomRunForm()
    if request.method == 'POST':
        custom_form = CustomRunForm(request.POST)
        if custom_form.is_valid():
            #
            sequencing_run = custom_form.save()
            #
            error_str = str()
            sequencing_run.basic_assembly = custom_form.cleaned_data.get('basic_assembly')
            sequencing_run.preprocess = custom_form.cleaned_data.get('preprocess')
            sequencing_run.run_name = custom_form.cleaned_data.get('run_name')
            sequencing_run.nextseq = custom_form.cleaned_data.get('nextseq')
            sequencing_run.save()
            blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                           account_key=settings.AZURE_ACCOUNT_KEY)
            blob_client.create_container(sequencing_run.run_name)
            return redirect('cowbat:upload_assembly_upload', sequencing_run_pk=sequencing_run.pk)

    return render(request,
                  'cowbat/custom_assembly_create.html',
                  {
                      'custom_form': custom_form,
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def custom_run_upload(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    if request.method == 'POST':
        container_name = sequencing_run.run_name
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        for i in range(0, len(request.FILES)):
            item = request.FILES.get('file[%d]' % i)
            # Calculate the checksum
            blob_headers = calculate_checksum(item=item)
            # Upload to blob storage
            blob_client.create_blob_from_bytes(
                container_name=container_name,
                blob_name=item.name,
                blob=item.read(),
                content_settings=blob_headers
            )

    return render(request,
                  'cowbat/custom_assembly_upload.html',
                  {
                      'sequencing_run': sequencing_run,
                  })
