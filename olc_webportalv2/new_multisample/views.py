import os
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django_tables2 import RequestConfig
from olc_webportalv2.new_multisample.models import ProjectMulti, Sample, SendsketchResult
from olc_webportalv2.new_multisample.forms import ProjectForm, JobForm
from olc_webportalv2.new_multisample import tasks
from olc_webportalv2.new_multisample.table import SendsketchTable
from background_task.models import Task
# Create your views here.


@login_required
def new_multisample(request):
    project_list = ProjectMulti.objects.filter(user=request.user)
    form = ProjectForm(request.POST)
    if request.method == 'POST':
        if form.is_valid():
            print('Form is valid.\n')

            # Have to do this in order to set the user to current logged in user
            new_entry = form.save(commit=False)

            # Pull active user
            new_entry.user = request.user

            # Save the form
            new_entry = form.save()

    return render(request,
                  'new_multisample/new_multisample.html',
                  {'project_list': project_list,
                   'form': form,
                   'project_id': ProjectMulti.pk,
                   'user': request.user
                   }
                  )


@login_required
def upload_samples(request, project_id):
    project = get_object_or_404(ProjectMulti, pk=project_id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    if request.method == 'POST':
        files = request.FILES.getlist('files')
        filenames = list()
        file_dict = dict()
        fasta_filenames = list()
        fasta_file_dict = dict()
        forward_id = request.POST['Forward_ID']
        reverse_id = request.POST['Reverse_ID']
        ProjectMulti.objects.filter(pk=project_id).update(forward_id=forward_id)
        ProjectMulti.objects.filter(pk=project_id).update(reverse_id=reverse_id)
        for item in files:
            if item.name.endswith('.fastq') or item.name.endswith('.fastq.gz'):
                filenames.append(item.name)
                file_dict[item.name] = item

            elif item.name.endswith('.fasta') or item.name.endswith('.fa'):  # TODO: Add more extensions
                fasta_filenames.append(item.name)
                fasta_file_dict[item.name] = item

        pairs = find_paired_reads(filenames, forward_id=forward_id, reverse_id=reverse_id)
        for pair in pairs:
            sample_name = pair[0].split(forward_id)[0]
            instance = Sample(file_R1=file_dict[pair[0]],
                              file_R2=file_dict[pair[1]],
                              title=sample_name,
                              project=project)
            if instance.file_R1.size >= 5000000 and instance.file_R2.size >= 5000000:
                instance.save()

        # Also upload FASTA files.
        for fasta in fasta_filenames:
            sample_name = fasta.split('.')[0]
            instance = Sample(file_fasta=fasta_file_dict[fasta],
                              title=sample_name,
                              project=project)
            instance.save()

        return redirect('new_multisample:project_detail', project_id=project_id)
    else:
        return render(request,
                      'new_multisample/upload_samples.html',
                      {'project': project},
                      )


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(ProjectMulti, pk=project_id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    if request.method == 'POST':
        form = JobForm(request.POST)
        # Save the form
        if form.is_valid():
            jobs_to_run = form.cleaned_data.get('jobs')
            print(jobs_to_run)
            if 'sendsketch' in jobs_to_run:
                for sample in project.samples.all():
                    if sample.sendsketch_status == 'Unprocessed':
                        if sample.file_fasta:
                            Sample.objects.filter(pk=sample.pk).update(sendsketch_status="In Queue")
                            tasks.run_sendsketch_fasta(fasta_file=sample.file_fasta.name,
                                                       sample_pk=sample.pk,
                                                       verbose_name='SendSketch on {} {}'.format(sample.title,
                                                                                                 sample.pk),
                                                       creator=request.user)
                        else:
                            file_path = os.path.dirname(str(sample.file_R1))
                            Sample.objects.filter(pk=sample.pk).update(sendsketch_status="In Queue")
                            tasks.run_sendsketch(read1=sample.file_R1.name,
                                                 read2=sample.file_R2.name,
                                                 sample_pk=sample.pk,
                                                 file_path=file_path,
                                                 verbose_name='SendSketch on {} {}'.format(sample.title,
                                                                                           sample.pk),
                                                 creator=request.user)

            if 'genesipprv2' in jobs_to_run:
                for sample in project.samples.all():
                    if sample.genesippr_status == 'Unprocessed' and not sample.file_fasta:
                        Sample.objects.filter(pk=sample.pk).update(genesippr_status="In Queue")
                        tasks.run_genesippr(sample_id=sample.pk,
                                            verbose_name='GeneSippr on {} {}'.format(sample.title,
                                                                                     sample.pk),
                                            creator=request.user)
                # Also get GeneSeekr going.
                for sample in project.samples.all():
                    if sample.file_fasta and sample.genesippr_status == 'Unprocessed':
                        Sample.objects.filter(pk=sample.pk).update(genesippr_status="In Queue")
                        tasks.run_geneseekr(fasta_file=sample.file_fasta.name,
                                            sample_pk=sample.pk,
                                            verbose_name='GeneSippr on {} {}'.format(sample.title,
                                                                                     sample.pk),
                                            creator=request.user)

            if 'confindr' in jobs_to_run:
                for sample in project.samples.all():
                    if sample.confindr_status == 'Unprocessed' and not sample.file_fasta:
                        Sample.objects.filter(pk=sample.pk).update(confindr_status="In Queue")
                        tasks.run_confindr(sample_id=sample.pk,
                                           verbose_name='ConFindr on {} {}'.format(sample.title,
                                                                                   sample.pk),
                                           creator=request.user)

            if 'genomeqaml' in jobs_to_run:
                for sample in project.samples.all():
                    if sample.genomeqaml_status == 'Unprocessed' and sample.file_fasta:
                        Sample.objects.filter(pk=sample.pk).update(genomeqaml_status="In Queue")
                        tasks.run_genomeqaml(fasta_file=sample.file_fasta.name,
                                             sample_pk=sample.pk,
                                             verbose_name='GenomeQAML on {} {}'.format(sample.title,
                                                                                       sample.pk),
                                             creator=request.user)

            if 'amrdetect' in jobs_to_run:
                for sample in project.samples.all():
                    if sample.amr_status == 'Unprocessed' and sample.file_fasta:
                        Sample.objects.filter(pk=sample.pk).update(amr_status="In Queue")
                        tasks.run_amr_fasta(sample_pk=sample.pk,
                                            verbose_name='AMR Detection on {} {}'.format(sample.title,
                                                                                         sample.pk),
                                            creator=request.user)
                    elif sample.amr_status == 'Unprocessed' and not sample.file_fasta:
                        Sample.objects.filter(pk=sample.pk).update(amr_status="In Queue")
                        tasks.run_amr(sample_pk=sample.pk,
                                      verbose_name='AMR Detection on {} {}'.format(sample.title,
                                                                                   sample.pk),
                                      creator=request.user)

            form = JobForm()

    else:
        form = JobForm()
        # Get a list of all tasks. The first item in the list should be the task that is currently processing.
        task = Task.objects.filter()
        try:
            currently_processing_task = task[0]
            # Now we need to parse the verbose name in order to update the relevant part of the Sample model in the DB.
            # Last element verbose name should always be sample title, and first element can tell us what task we're
            # looking at.
            try:
                sample_pk = int(currently_processing_task.verbose_name.split()[-1])
                task_type = currently_processing_task.verbose_name.split()[0]
                if task_type == 'AMR':
                    Sample.objects.filter(pk=sample_pk).update(amr_status='Processing')
                elif task_type == 'GenomeQAML':
                    Sample.objects.filter(pk=sample_pk).update(genomeqaml_status='Processing')
                elif task_type == 'ConFindr':
                    Sample.objects.filter(pk=sample_pk).update(confindr_status='Processing')
                elif task_type == 'GeneSippr':
                    Sample.objects.filter(pk=sample_pk).update(genesippr_status='Processing')
                elif task_type == 'SendSketch':
                    Sample.objects.filter(pk=sample_pk).update(sendsketch_status='Processing')
            except AttributeError:
                pass
        # Make sure we don't crash if there aren't any tasks to be processed.
        except IndexError:
            pass

    return render(request,
                  'new_multisample/project_detail.html',
                  {'project': project,
                   'form': form,
                   'user': request.user},
                  )


@login_required
def genomeqaml_detail(request, sample_id):
    sample = get_object_or_404(Sample, pk=sample_id)
    if request.user != sample.project.user:
        return redirect('new_multisample:forbidden')
    return render(request,
                  'new_multisample/genomeqaml_detail.html',
                  {'sample': sample},
                  )


@login_required
def sendsketch_results_table(request, sample_id):
    try:
        sample = SendsketchResult.objects.filter(sample=Sample.objects.get(pk=sample_id)).exclude(rank='N/A')
        s = get_object_or_404(Sample, pk=sample_id)
        if request.user != s.project.user:
            return redirect('new_multisample:forbidden')
        sendsketch_results_table_ = SendsketchTable(SendsketchResult.objects.all())
        base_project = Sample.objects.get(pk=sample_id)
        RequestConfig(request).configure(sendsketch_results_table_)
    except ObjectDoesNotExist:
        sendsketch_results_table_ = None
        sample = None
        base_project = None

    return render(request,
                  'new_multisample/sendsketch_results_table.html',
                  {'sendsketch_results_table': sendsketch_results_table_,
                   'project': sample,
                   'base_project': base_project
                   }
                  )


@login_required
def confindr_results_table(request, project_id):
    project = get_object_or_404(ProjectMulti, pk=project_id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    return render(request,
                  'new_multisample/confindr_results_table.html',
                  {'project': project},
                  )


@login_required
def genomeqaml_results(request, project_id):
    project = get_object_or_404(ProjectMulti, pk=project_id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    return render(request,
                  'new_multisample/genomeqaml_results.html',
                  {'project': project},
                  )


@login_required
def display_genesippr_results(request, project_id):
    project = get_object_or_404(ProjectMulti, pk=project_id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    return render(request,
                  'new_multisample/display_genesippr_results.html',
                  {'project': project},
                  )


@login_required
def project_remove(request, project_id):
    project = get_object_or_404(ProjectMulti, pk=project_id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    project.delete()
    return redirect('new_multisample:new_multisample')


@login_required
def project_remove_confirm(request, project_id):
    project = get_object_or_404(ProjectMulti, pk=project_id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    return render(request,
                  'new_multisample/confirm_project_delete.html',
                  {'project': project},
                  )


@login_required
def sample_remove(request, sample_id):
    sample = get_object_or_404(Sample, pk=sample_id)
    project = get_object_or_404(ProjectMulti, pk=sample.project.id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    sample.delete()
    return redirect('new_multisample:project_detail', project_id=project.id)


@login_required
def sample_remove_confirm(request, sample_id):
    sample = get_object_or_404(Sample, pk=sample_id)
    project = get_object_or_404(ProjectMulti, pk=sample.project.id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    return render(request,
                  'new_multisample/confirm_sample_delete.html',
                  {'sample': sample,
                   'project': project},
                  )


@login_required
def gdcs_detail(request, sample_id):
    sample = get_object_or_404(Sample, pk=sample_id)
    if request.user != sample.project.user:
        return redirect('new_multisample:forbidden')
    return render(request,
                  'new_multisample/gdcs_detail.html',
                  {'sample': sample},
                  )


@login_required
def amr_detail(request, sample_id):
    sample = get_object_or_404(Sample, pk=sample_id)
    species = 'Unknown'
    result_dict = dict()
    if request.user != sample.project.user:
        return redirect('new_multisample:forbidden')
    for amr_result in sample.amr_results.all():
        result_dict = amr_result.results_dict
        species = amr_result.species
    caption = [sample.title, species]
    return render(request,
                  'new_multisample/amr_detail.html',
                  {'sample': sample,
                   'results': result_dict,
                   'caption': caption},
                  )


@login_required
def task_queue(request):
    task_count = Task.objects.count()
    task = Task.objects.filter()  # Get ALL the tasks belonging to specified user!
    # Need to: Figure out a way to assign verbose names to tasks,
    # and also to generate a list of tasks with complete

    # Get task offset for showing position in queue.
    try:
        offset = task[0].pk - 1
        offset = offset * -1  # Make negative so we can do addition that's really subtraction in the template
    except IndexError:  # Handle the case that there are no tasks.
        offset = 0

    task_list = list()
    for item in task:
        if item.creator == request.user:
            task_list.append(item)
    return render(request,
                  'new_multisample/task_queue.html',
                  {'task': task_list,
                   'task_count': task_count,
                   'offset': offset},
                  )


@login_required
def only_project_table(request, project_id):
    project = get_object_or_404(ProjectMulti, pk=project_id)
    if request.user != project.user:
        return redirect('new_multisample:forbidden')
    task = Task.objects.filter()
    try:
        try:
            currently_processing_task = task[0]
            # Now we need to parse the verbose name in order to update the relevant part of the Sample model in the DB.
            # Last element verbose name should always be sample title, and first element can tell us what task we're
            # looking at.
            sample_pk = int(currently_processing_task.verbose_name.split()[-1])
            task_type = currently_processing_task.verbose_name.split()[0]
            if task_type == 'AMR':
                Sample.objects.filter(pk=sample_pk).update(amr_status='Processing')
            elif task_type == 'GenomeQAML':
                Sample.objects.filter(pk=sample_pk).update(genomeqaml_status='Processing')
            elif task_type == 'ConFindr':
                Sample.objects.filter(pk=sample_pk).update(confindr_status='Processing')
            elif task_type == 'GeneSippr':
                Sample.objects.filter(pk=sample_pk).update(genesippr_status='Processing')
            elif task_type == 'SendSketch':
                Sample.objects.filter(pk=sample_pk).update(sendsketch_status='Processing')
        except AttributeError:
            pass
    # Make sure we don't crash if there aren't any tasks to be processed.
    except IndexError:
        pass
    return render(request,
                  'new_multisample/only_project_table.html',
                  {'project': project},
                  )


def forbidden(request):
    return render(request,
                  'new_multisample/forbidden.html')


def find_paired_reads(filelist, forward_id='_R1', reverse_id='_R2'):
    pairs = list()
    for filename in filelist:
        if forward_id in filename and filename.replace(forward_id, reverse_id) in filelist:
            pairs.append([filename, filename.replace(forward_id, reverse_id)])
    return pairs


