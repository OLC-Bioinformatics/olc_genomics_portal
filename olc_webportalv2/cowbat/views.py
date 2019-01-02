# Django-related imports
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.conf import settings
# Standard libraries
import logging
import fnmatch
import os
# Portal-specific things.
from olc_webportalv2.cowbat.models import SequencingRun, DataFile
from olc_webportalv2.cowbat.forms import RunNameForm
from olc_webportalv2.cowbat.tasks import run_cowbat_batch
# Azure!
from azure.storage.blob import BlockBlobService
import azure.batch.batch_service_client as batch
import azure.batch.batch_auth as batch_auth
import azure.batch.models as batchmodels

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
            percent_completed = int(100.0 * (current_subfolders/final_num_reports))

    except batchmodels.BatchErrorException:  # Means task and job have not yet been created
        percent_completed = 1
    return percent_completed


def check_uploaded_seqids(sequencing_run):
    container_name = str(sequencing_run).lower().replace('_', '-')
    blob_client = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                   account_name=settings.AZURE_ACCOUNT_NAME)
    blob_filenames = list()
    blobs = blob_client.list_blobs(container_name=container_name)
    for blob in blobs:
        blob_filenames.append(blob.name)
    seqids_to_remove = list()
    for seqid in sequencing_run.seqids:
        forward_reads = fnmatch.filter(blob_filenames, seqid + '*_R1*')
        reverse_reads = fnmatch.filter(blob_filenames, seqid + '*_R2*')
        if len(forward_reads) == 1 and len(reverse_reads) == 1:
            seqids_to_remove.append(seqid)
    for seqid in seqids_to_remove:
        sequencing_run.seqids.remove(seqid)
    sequencing_run.save()


# Create your views here.
@login_required
def cowbat_processing(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    if sequencing_run.status == 'Unprocessed':
        SequencingRun.objects.filter(pk=sequencing_run.pk).update(status='Processing')
        run_cowbat_batch(sequencing_run_pk=sequencing_run.pk)

    # Find percent complete (approximately). Not sure that having calls to azure batch API in views is a good thing.
    # Will have to see if performance is terrible because of it.
    if sequencing_run.status == 'Processing':
        progress = find_percent_complete(sequencing_run)
    else:
        progress = 1
    return render(request,
                  'cowbat/cowbat_processing.html',
                  {
                      'sequencing_run': sequencing_run,
                      'progress': str(progress)
                  })


@login_required
def assembly_home(request):
    sequencing_runs = SequencingRun.objects.filter()
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
                sequencing_run, created = SequencingRun.objects.update_or_create(run_name=form.cleaned_data.get('run_name'),
                                                                                 seqids=list())
            else:
                sequencing_run = SequencingRun.objects.get(run_name=form.cleaned_data.get('run_name'))
            files = [request.FILES.get('file[%d]' % i) for i in range(0, len(request.FILES))]
            container_name = sequencing_run.run_name.lower().replace('_', '-')
            blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                           account_key=settings.AZURE_ACCOUNT_KEY)
            blob_client.create_container(container_name)
            for item in files:
                blob_client.create_blob_from_bytes(container_name=container_name,
                                                   blob_name=item.name,
                                                   blob=item.read())
                if item.name == 'SampleSheet.csv':
                    instance = DataFile(sequencing_run=sequencing_run,
                                        data_file=item)
                    instance.save()
                    with open('olc_webportalv2/media/{run_name}/SampleSheet.csv'.format(run_name=str(sequencing_run))) as f:
                        lines = f.readlines()
                    seqid_start = False
                    seqid_list = list()
                    for i in range(len(lines)):
                        if seqid_start:
                            seqid = lines[i].split(',')[0]
                            seqid_list.append(seqid)
                        if 'Sample_ID' in lines[i]:
                            seqid_start = True
                    SequencingRun.objects.filter(pk=sequencing_run.pk).update(seqids=seqid_list)
            return redirect('cowbat:upload_interop', sequencing_run_pk=sequencing_run.pk)
    return render(request,
                  'cowbat/upload_metadata.html',
                  {
                      'form': form
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

                blob_client.create_blob_from_bytes(container_name=container_name,
                                                   blob_name=os.path.join('InterOp', item.name),
                                                   blob=item.read())
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
    if request.method == 'POST':
        container_name = sequencing_run.run_name.lower().replace('_', '-')
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_client.create_container(container_name)
        for i in range(0, len(request.FILES)):
            item = request.FILES.get('file[%d]' % i)
            blob_client.create_blob_from_bytes(container_name=container_name,
                                               blob_name=item.name,
                                               blob=item.read())

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
