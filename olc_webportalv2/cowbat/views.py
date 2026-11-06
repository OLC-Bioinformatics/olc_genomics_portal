
# Standard libraries
import csv
import json
import logging
import fnmatch
import os
import re
import tempfile
from django.http import HttpResponseNotAllowed, JsonResponse

# Django-related imports
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

# Task Management
from kombu import Queue

# Autocomplete
from dal import autocomplete

# Local imports
from olc_webportalv2.cowbat.models import (
    DataFile,
    ResearchRun,
    SequencingRun,
    SummaryMetadata
)
from olc_webportalv2.cowbat.forms import (
    CustomRunForm,
    RealTimeForm,
    RunNameForm,
    RunRequestForm
)
from olc_webportalv2.common.methods import create_batch_client
from olc_webportalv2.common.batch_client import BatchServiceError
from olc_webportalv2.cowbat.tasks import (
    escape_ansi,
    rename_research_container_task,
    run_cowbat_batch,
)
from olc_webportalv2.cowbat.utils import normalize_research_run_name
from olc_webportalv2.common.research_storage import get_research_storage
from olc_webportalv2.filezone.methods import calculate_checksum
from olc_webportalv2.common.storage_client import get_storage_client
from olc_webportalv2.geneseekr.forms import EmailForm

# Set up logging
log = logging.getLogger(__name__)


def find_percent_complete(sequencing_run):
    try:
        job_id = str(sequencing_run).lower().replace('_', '-')
        batch_client = create_batch_client()
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

    except BatchServiceError as exc:  # Means task and job have not yet been created
        if exc.status_code != 404:
            raise
        percent_completed = 1
    return percent_completed


def find_percentage_complete(sequencing_run):
    job_id = sequencing_run.job_id
    batch_client = create_batch_client()

    # Initialise a dictionary to store the stderr file information
    contents = {}
    try:
        node_files = batch_client.file.list_from_task(
            job_id=job_id,
            task_id=sequencing_run.task_id,
            recursive=True
        )
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
    except BatchServiceError as exc:
        # The run hasn't started assembling yet
        if exc.status_code == 404:
            return
        raise

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
    storage = get_storage_client()
    blob_filenames = [blob.name for blob in storage.list_objects(container_name)]
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
            storage = get_storage_client()
            storage.ensure_container(container_name)
            for item in files:
                # Calculate the checksum
                blob_md5 = calculate_checksum(item=item)
                # Upload to blob storage
                storage.upload_bytes(
                    container_name,
                    item.name,
                    item.read(),
                    content_type=getattr(item, 'content_type', None),
                    content_md5=blob_md5,
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
            get_storage_client().upload_file(
                container_name, 'SampleSheet.csv', samplesheet_path,
                content_type='text/csv',
            )
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
        storage = get_storage_client()
        storage.ensure_container(container_name)
        files = [request.FILES.get('file[%d]' % i) for i in range(0, len(request.FILES))]
        for item in files:
            # Calculate the checksum
            blob_md5 = calculate_checksum(item=item)
            # Upload to blob storage
            storage.upload_bytes(
                container_name,
                os.path.join('InterOp', item.name),
                item.read(),
                content_type=getattr(item, 'content_type', None),
                content_md5=blob_md5,
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
        storage = get_storage_client()
        storage.ensure_container(container_name)
        for i in range(0, len(request.FILES)):
            item = request.FILES.get('file[%d]' % i)
            # Calculate the checksum
            blob_md5 = calculate_checksum(item=item)
            # Upload to blob storage
            storage.upload_bytes(
                container_name,
                item.name,
                item.read(),
                content_type=getattr(item, 'content_type', None),
                content_md5=blob_md5,
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
    try:
        container_names = get_research_storage().list_containers()
    except Exception:
        log.exception('Could not list research-run containers from Blob Storage.')
        container_names = ResearchRun.objects.values_list('run_name', flat=True)
    return sorted({
        name for name in container_names
        if re.fullmatch(r'\d{5,6}-[a-z0-9]+(?:-[a-z0-9]+)*', name)
        and not name.endswith('-output')
    })


class RunAutoCompleter(autocomplete.Select2ListView):

    def __init__(self, **kwargs):
        self.category = 'run'
        super().__init__(**kwargs)

    def get_list(self):
        run_names = find_research_runs()
        if self.q:
            query = self.q.lower()
            run_names = [name for name in run_names if query in name.lower()]
        return run_names


def _research_run_selection_form():
    run_names = find_research_runs()
    for run_name in run_names:
        ResearchRun.objects.get_or_create(run_name=run_name)
    form = RunRequestForm()
    if run_names:
        form.fields['run_name'].queryset = ResearchRun.objects.filter(
            run_name__in=run_names
        )
    return form


def _research_run_file_names(storage, container_name):
    entries = []
    list_entries = getattr(storage, 'list_file_entries', None)
    if list_entries:
        entries = list(list_entries(container_name) or [])
    if not entries:
        return sorted(set(storage.list_files(container_name)))

    names = [entry['name'] for entry in entries]
    zero_byte_names = {
        entry['name'].rstrip('/')
        for entry in entries
        if entry.get('size') == 0
    }

    def is_folder_marker(entry):
        name = entry['name']
        normalized_name = name.rstrip('/')
        has_children = any(
            other.startswith(normalized_name + '/')
            for other in names
            if other != name
        )
        return (
            entry.get('size') == 0
            and (name.endswith('/') or normalized_name in zero_byte_names and has_children)
        )

    return sorted({
        entry['name'] for entry in entries if not is_folder_marker(entry)
    })


def validate_research_files(storage, container_name, files):
    """Return complete sample IDs and validation errors for FASTQ read pairs."""
    fastq_pattern = re.compile(
        r'^(?P<sample>.+?)(?:_S\d+)?(?:_L\d{3})?_R(?P<read>[12])'
        r'(?:_\d+)?\.f(?:ast)?q(?:\.gz)?$',
        re.IGNORECASE,
    )
    reads_by_sample = {}
    for blob_name in files:
        filename = blob_name.rsplit('/', 1)[-1]
        match = fastq_pattern.fullmatch(filename)
        if not match:
            continue
        sample = match.group('sample')
        read_name = 'R' + match.group('read')
        reads_by_sample.setdefault(sample, {'R1': [], 'R2': []})[read_name].append(
            blob_name
        )

    seqids = []
    errors = []
    for sample in sorted(reads_by_sample):
        sample_reads = reads_by_sample[sample]
        for read_name in ('R1', 'R2'):
            if len(sample_reads[read_name]) > 1:
                errors.append('Duplicate {0} file for {1}.'.format(read_name, sample))
            elif not sample_reads[read_name]:
                errors.append('{0} is missing {1}.'.format(sample, read_name))
        if sample_reads['R1'] and sample_reads['R2']:
            seqids.append(sample)

    sample_sheet = next(
        (
            name for name in files
            if name.rsplit('/', 1)[-1].lower() == 'samplesheet.csv'
        ),
        None,
    )
    if sample_sheet:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temporary_file:
                temporary_path = temporary_file.name
            storage.download_blob(container_name, sample_sheet, temporary_path)
            with open(temporary_path, 'r', encoding='utf-8-sig', newline='') as sample_sheet_file:
                rows = list(csv.reader(sample_sheet_file))
            data_section = next(
                (
                    index for index, row in enumerate(rows)
                    if row and row[0].strip().lower() == '[data]'
                ),
                -1,
            )
            sample_sheet_rows = rows[data_section + 1:] if data_section >= 0 else rows
            header_index = next(
                (
                    index for index, row in enumerate(sample_sheet_rows)
                    if any(cell.strip() for cell in row)
                ),
                None,
            )
            if header_index is None:
                errors.append('SampleSheet.csv has no data header.')
            else:
                header = [
                    re.sub(r'[^a-z0-9]', '', cell.strip().lower())
                    for cell in sample_sheet_rows[header_index]
                ]
                sample_column = next(
                    (
                        index for index, name in enumerate(header)
                        if name in ('sampleid', 'sample')
                    ),
                    None,
                )
                if sample_column is None:
                    sample_column = next(
                        (
                            index for index, name in enumerate(header)
                            if name == 'samplename'
                        ),
                        None,
                    )
                if sample_column is None:
                    errors.append(
                        'SampleSheet.csv must contain a Sample_ID or Sample_Name column.'
                    )
                else:
                    sample_sheet_ids = {
                        row[sample_column].strip()
                        for row in sample_sheet_rows[header_index + 1:]
                        if len(row) > sample_column and row[sample_column].strip()
                    }
                    for sample in sorted(sample_sheet_ids - set(seqids)):
                        errors.append(
                            '{0} is listed in SampleSheet.csv but has no complete FASTQ pair.'.format(
                                sample
                            )
                        )
        except Exception as exc:
            log.exception('Could not validate SampleSheet.csv in %s.', container_name)
            errors.append('Could not read SampleSheet.csv: {0}'.format(exc))
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    return seqids, errors


def _render_manage_assembly_run(request, sequencing_run, storage, form=None):
    try:
        files = _research_run_file_names(
            storage,
            sequencing_run.container or sequencing_run.run_name,
        )
    except Exception:
        log.exception(
            'Could not list files for research run %s.',
            sequencing_run.pk,
        )
        files = []
        messages.error(request, _('Could not list files in this research run.'))
    return render(
        request,
        'cowbat/manage_research_assembly.html',
        {
            'sequencing_run': sequencing_run,
            'container_name': sequencing_run.container or sequencing_run.run_name,
            'files': files,
            'form': form or CustomRunForm(instance=sequencing_run),
        },
    )


@login_required
def manage_assembly(request):
    selection_form = _research_run_selection_form()
    raw_run_name = request.GET.get('run_name', '').strip()
    if not raw_run_name:
        return render(
            request,
            'cowbat/manage_research_assembly.html',
            {
                'selection_form': selection_form,
                'sequencing_run': None,
            },
        )

    try:
        run_name = normalize_research_run_name(raw_run_name)
    except ValueError as exc:
        selection_form.add_error('run_name', str(exc))
        return render(
            request,
            'cowbat/manage_research_assembly.html',
            {
                'selection_form': selection_form,
                'sequencing_run': None,
            },
        )

    sequencing_run = SequencingRun.objects.filter(
        Q(run_name__iexact=run_name) | Q(container__iexact=run_name)
    ).first()
    if sequencing_run:
        return redirect(
            'cowbat:manage_assembly_run',
            sequencing_run_pk=sequencing_run.pk,
        )

    try:
        container_names = get_research_storage().list_containers()
    except Exception:
        log.exception('Could not verify research container %s.', run_name)
        selection_form.add_error(
            None,
            _('Could not verify the run in Blob Storage. Please try again.'),
        )
        return render(
            request,
            'cowbat/manage_research_assembly.html',
            {
                'selection_form': selection_form,
                'sequencing_run': None,
            },
        )

    if run_name not in container_names:
        selection_form.add_error('run_name', _('No existing research run has that name.'))
        return render(
            request,
            'cowbat/manage_research_assembly.html',
            {
                'selection_form': selection_form,
                'sequencing_run': None,
            },
        )

    sequencing_run = SequencingRun.objects.create(
        user=request.user,
        run_name=run_name,
        container=run_name,
        workflow_state='uploading',
    )
    return redirect(
        'cowbat:manage_assembly_run',
        sequencing_run_pk=sequencing_run.pk,
    )


@login_required
def manage_assembly_run(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    storage = get_research_storage()
    form = CustomRunForm(instance=sequencing_run)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'rename':
            target_name = ''
            try:
                target_name = normalize_research_run_name(
                    request.POST.get('run_name', '')
                )
            except ValueError as exc:
                form.add_error(None, str(exc))

            if target_name:
                current_name = sequencing_run.container or sequencing_run.run_name
                if target_name == current_name:
                    messages.info(request, _('The research run already has that name.'))
                    return redirect(
                        'cowbat:manage_assembly_run',
                        sequencing_run_pk=sequencing_run.pk,
                    )
                if sequencing_run.workflow_state in (
                    'submitting',
                    'submitted',
                    'processing',
                    'stopping',
                ) or sequencing_run.status.lower() == 'processing':
                    form.add_error(
                        None,
                        _('A run cannot be renamed after processing has started.'),
                    )
                else:
                    try:
                        container_names = get_research_storage().list_containers()
                    except Exception:
                        log.exception(
                            'Could not check whether research container %s exists.',
                            target_name,
                        )
                        container_names = None

                    if container_names is not None and target_name in container_names:
                        form.add_error(
                            None,
                            _('That container name is already in use.'),
                        )
                    else:
                        existing_run = SequencingRun.objects.filter(
                            Q(run_name__iexact=target_name)
                            | Q(container__iexact=target_name)
                        ).exclude(pk=sequencing_run.pk).exists()
                        if existing_run:
                            form.add_error(
                                None,
                                _('That container name is already in use.'),
                            )
                        else:
                            sequencing_run.rename_status = 'queued'
                            sequencing_run.rename_target = target_name
                            sequencing_run.save(
                                update_fields=['rename_status', 'rename_target']
                            )
                            try:
                                rename_research_container_task.apply_async(
                                    args=[
                                        sequencing_run.pk,
                                        current_name,
                                        target_name,
                                    ],
                                    queue='cowbat',
                                )
                            except Exception as exc:
                                log.exception(
                                    'Could not queue rename for research run %s.',
                                    sequencing_run.pk,
                                )
                                sequencing_run.rename_status = 'failed'
                                sequencing_run.errors = (
                                    sequencing_run.errors or []
                                ) + ['Rename failed: {0}'.format(str(exc)[:220])]
                                sequencing_run.save(
                                    update_fields=['rename_status', 'errors']
                                )
                                form.add_error(
                                    None,
                                    _('Could not queue the research run rename.'),
                                )
                            else:
                                return redirect(
                                    'cowbat:manage_assembly_run',
                                    sequencing_run_pk=sequencing_run.pk,
                                )
        elif action == 'submit':
            form = CustomRunForm(request.POST, instance=sequencing_run)
            if form.is_valid():
                container_name = sequencing_run.container or sequencing_run.run_name
                try:
                    storage_files = storage.list_files(container_name)
                    seqids, validation_errors = validate_research_files(
                        storage,
                        container_name,
                        storage_files,
                    )
                except Exception:
                    log.exception(
                        'Could not validate files for research run %s.',
                        sequencing_run.pk,
                    )
                    form.add_error(
                        None,
                        _('Could not validate the files in this research run.'),
                    )
                else:
                    for error in validation_errors:
                        form.add_error(None, error)
                    if not seqids:
                        form.add_error(
                            None,
                            _('At least one complete R1/R2 FASTQ pair is required.'),
                        )
                    if not form.errors:
                        sequencing_run = form.save(commit=False)
                        sequencing_run.container = container_name
                        sequencing_run.seqids = seqids
                        sequencing_run.status = 'Unprocessed'
                        sequencing_run.workflow_state = 'uploading'
                        sequencing_run.save()
                        return redirect(
                            'cowbat:cowbat_processing',
                            sequencing_run_pk=sequencing_run.pk,
                        )
        else:
            form.add_error(None, _('Unknown research-run action.'))

    return _render_manage_assembly_run(
        request,
        sequencing_run,
        storage,
        form=form,
    )


@login_required
def delete_research_files(request, sequencing_run_pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    storage = get_research_storage()
    container_name = sequencing_run.container or sequencing_run.run_name
    try:
        available_files = set(storage.list_files(container_name))
        requested_files = (
            available_files
            if request.POST.get('delete_all') == '1'
            else set(request.POST.getlist('files')) & available_files
        )
        for blob_name in sorted(requested_files):
            storage.delete_file(container_name, blob_name)
    except Exception:
        log.exception('Could not delete research-run files for run %s.', sequencing_run.pk)
        messages.error(request, _('Could not delete the selected research-run files.'))
    else:
        messages.success(
            request,
            _('Deleted {0} file(s).').format(len(requested_files)),
        )
    return redirect(
        'cowbat:manage_assembly_run',
        sequencing_run_pk=sequencing_run.pk,
    )


@login_required
def research_assembly_rename_status(request, sequencing_run_pk):
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    error = next(
        (
            value for value in reversed(sequencing_run.errors or [])
            if value.startswith('Rename failed:')
        ),
        '',
    )
    return JsonResponse({
        'status': sequencing_run.rename_status or 'idle',
        'target_name': sequencing_run.rename_target,
        'complete': sequencing_run.rename_status == 'complete',
        'error': error,
    })


@login_required
def upload_sequence_blob_sas(request, sequencing_run_pk):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    sequencing_run = get_object_or_404(SequencingRun, pk=sequencing_run_pk)
    filename = request.GET.get('filename', '')
    if (
        not filename
        or filename in ('.', '..')
        or '/' in filename
        or '\\' in filename
        or len(filename) > 255
        or any(ord(character) < 32 for character in filename)
    ):
        return JsonResponse({'error': 'A valid file name is required.'}, status=400)
    try:
        upload_url = get_research_storage().upload_url(
            sequencing_run.container or sequencing_run.run_name,
            filename,
        )
    except Exception:
        log.exception(
            'Could not create an upload URL for research run %s.',
            sequencing_run.pk,
        )
        return JsonResponse(
            {'error': 'Could not prepare the file upload.'},
            status=503,
        )
    return JsonResponse({'upload_url': upload_url, 'blob_name': filename})


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
            sequencing_run = custom_form.save(commit=False)
            sequencing_run.user = request.user
            sequencing_run.container = sequencing_run.run_name
            sequencing_run.workflow_state = 'uploading'
            try:
                get_research_storage().ensure_container(sequencing_run.container)
            except Exception:
                log.exception(
                    'Could not create research container %s.',
                    sequencing_run.container,
                )
                custom_form.add_error(
                    None,
                    _('Could not create the research-run container.'),
                )
            else:
                sequencing_run.save()
                return redirect(
                    'cowbat:upload_assembly_upload',
                    sequencing_run_pk=sequencing_run.pk,
                )

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
        storage = get_storage_client()
        for i in range(0, len(request.FILES)):
            item = request.FILES.get('file[%d]' % i)
            # Calculate the checksum
            blob_md5 = calculate_checksum(item=item)
            # Upload to blob storage
            storage.upload_bytes(
                container_name,
                item.name,
                item.read(),
                content_type=getattr(item, 'content_type', None),
                content_md5=blob_md5,
            )

    return render(request,
                  'cowbat/custom_assembly_upload.html',
                  {
                      'sequencing_run': sequencing_run,
                  })
