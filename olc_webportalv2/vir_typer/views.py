from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.forms.formsets import formset_factory
from azure.storage.blob import BlockBlobService
from django.contrib import messages
from weasyprint import HTML, CSS
from django.conf import settings
import json
import os

# VirusTyper-specific code
from .forms import VirTyperProjectForm, VirTyperSampleForm, BaseVirTyperSampleFormSet
from .models import VirTyperFiles, VirTyperProject, VirTyperRequest, VirTyperResults
from .tasks import run_vir_typer


# Vir_Typer Views
@csrf_exempt  # needed or IE explodes
@login_required
def vir_typer_home(request):
    vir_typer_projects = VirTyperProject.objects.filter(user=request.user)

    if request.method == "POST":
        if request.POST.get('delete'): 
            query = VirTyperProject.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'vir_typer/vir_typer_home.html',
                  {
                      'vir_typer_projects': vir_typer_projects
                  })


@login_required
def vir_typer_request(request):
    tombstone_form = VirTyperProjectForm()
    # Create a sample form set using the sample form and formset_factory
    sample_form_set_factory = formset_factory(VirTyperSampleForm, formset=BaseVirTyperSampleFormSet, max_num=10)
    if request.method == 'POST':
        tombstone_form = VirTyperProjectForm(request.POST)
        sample_form_set = sample_form_set_factory(request.POST)
        if tombstone_form.is_valid() and sample_form_set.is_valid():
            project_name = tombstone_form.cleaned_data.get('project_name')
            vir_typer_project = VirTyperProject.objects.create(project_name=project_name,
                                                               user=request.user,)

            sample_data = list()
            for form in sample_form_set:
                sample_name = form.cleaned_data.get('sample_name')
                date_received = form.cleaned_data.get('date_received')
                lab_id = form.cleaned_data.get('lab_ID')
                lsts_id = form.cleaned_data.get('LSTS_ID')
                isolate_source = form.cleaned_data.get('isolate_source')
                putative_classification = form.cleaned_data.get('putative_classification')
                analyst_name = form.cleaned_data.get('analyst_name')
                subunit = form.cleaned_data.get('subunit')
                if sample_name and date_received and lab_id and lsts_id and isolate_source and putative_classification\
                        and analyst_name and subunit:
                    sample_data.append(VirTyperRequest(sample_name=sample_name,
                                                       project_name_id=vir_typer_project.pk,
                                                       date_received=date_received,
                                                       lab_ID=lab_id,
                                                       LSTS_ID=lsts_id,
                                                       isolate_source=isolate_source,
                                                       putative_classification=putative_classification,
                                                       analyst_name=analyst_name,
                                                       subunit=subunit))

            if sample_data:
                vir_typer_project.save()
                VirTyperRequest.objects.bulk_create(sample_data)
                message_str = _('You have created project ')
                messages.success(request, message_str + '{project}'
                                 .format(project=vir_typer_project.project_name))

                return redirect('vir_typer:vir_typer_upload',
                                vir_typer_pk=vir_typer_project.pk)
# If forms aren't valid, error messages
        else:
            out_str = str()
            if tombstone_form.cleaned_data.get('project_name') is None:
                out_str += tombstone_form.errors['project_name']
            out_str += sample_form_set.non_form_errors()
            messages.error(request, out_str)
    else:
        sample_form_set = sample_form_set_factory()
    return render(request,
                  'vir_typer/vir_typer_create.html',
                  {
                      'tombstone_form': tombstone_form,
                      'sample_formset': sample_form_set,
                  })


@login_required
def vir_typer_upload(request, vir_typer_pk):
    vir_typer_project = get_object_or_404(VirTyperProject, pk=vir_typer_pk)
    vir_typer_samples = list(VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk))
    sample_names = list()
    for sample in vir_typer_samples:
        sample_names.append(str(sample.sample_name))
    if request.method == 'POST':
        seq_files = [request.FILES.get('file[%d]' % i) for i in range(0, len(request.FILES))]
        if seq_files:
            container_name = VirTyperProject.objects.get(pk=vir_typer_pk).container_namer()
            blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                           account_key=settings.AZURE_ACCOUNT_KEY)
            blob_client.create_container(container_name)
            for item in seq_files:
                blob_client.create_blob_from_bytes(container_name=container_name,
                                                   blob_name=item.name,
                                                   blob=item.read())
            for sample in vir_typer_samples:
                sample_name = str(sample.sample_name)
                for seq_file in seq_files:
                    if sample_name in str(seq_file):

                        vir_files = VirTyperFiles(sample_name_id=sample.pk,
                                                  sequence_file=seq_file)
                        vir_files.save()
            vir_typer_project.status = 'Processing'
            vir_typer_project.save()
            run_vir_typer.apply_async(queue='cowbat', args=(vir_typer_pk,), countdown=10)
        return redirect('vir_typer:vir_typer_home')
    return render(request,
                  'vir_typer/vir_typer_upload_sequences.html',
                  {
                      'vir_typer_project': vir_typer_project,
                      'vir_typer_samples': vir_typer_samples,
                      'vir_typer_sample_names': sample_names
                  })


def parse_report(vir_typer_json, vir_typer_samples):
    """
    Parse the supplied JSON-formatted report, and populate the database with the values
    :param vir_typer_json:
    :param vir_typer_samples:
    :return:
    """
    vir_typer_json = vir_typer_json.replace("\'", "\"")
    json_data = json.loads(vir_typer_json)
    for sample in vir_typer_samples:
        vir_files = list(VirTyperFiles.objects.filter(sample_name__pk=sample.pk))
        for vir_file in vir_files:
            vir_sample = os.path.splitext(vir_file.sequence_file)[0]
            try:
                file_dict = json_data[vir_sample]
                vir_results = VirTyperResults(sequence_file_id=vir_file.id,
                                              allele=file_dict['allele'],
                                              orientation=file_dict['orientation'],
                                              forward_primer=[key for key in file_dict['primer_matches']['forward']][0],
                                              reverse_primer=[key for key in file_dict['primer_matches']['reverse']][0],
                                              trimmed_sequence=file_dict['trimmed_seq'],
                                              trimmed_sequence_len=len(file_dict['trimmed_seq']),
                                              trimmed_quality_max=file_dict['trimmed_quality_max'],
                                              trimmed_quality_mean=file_dict['trimmed_quality_mean'],
                                              trimmed_quality_min=file_dict['trimmed_quality_min'],
                                              trimmed_quality_stdev=file_dict['trimmed_quality_stdev'])
                vir_results.save()
            except KeyError:
                pass


def sequence_html_string(sequence, consensus_sequence):
    consensus_span_dict = {
        'A': "<span style='color:white;background-color:green;font-family:courier;'>A</span>",
        'C': "<span style='color:black;background-color:blue;font-family:courier;'>C</span>",
        'G': "<span style='color:white;background-color:black;font-family:courier;'>G</span>",
        'T': "<span style='color:black;background-color:red;font-family:courier;'>T</span>",
    }
    no_match_span_dict = {
        'A': "<span style='color:green;background-color:white;font-family:courier;'>A</span>",
        'C': "<span style='color:blue;background-color:white;font-family:courier;'>C</span>",
        'G': "<span style='color:black;background-color:white;font-family:courier;'>G</span>",
        'T': "<span style='color:red;background-color:white;font-family:courier;'>T</span>",
    }
    html_string = str()
    variable_locations = 0
    for i, nt in enumerate(sequence):
        if consensus_sequence[i].upper() != 'X':
            try:
                html_code = consensus_span_dict[nt.upper()]
            except KeyError:
                html_code = "<span style='color:purple;background-color:white;font-family:courier;'>{nt}</span>"\
                    .format(nt=nt.upper())
        else:
            variable_locations += 1
            try:
                html_code = no_match_span_dict[nt.upper()]
            except KeyError:
                html_code = "<span style='color:purple;background-color:white;font-family:courier;'>{nt}</span>".format(
                    nt=nt.upper())
        html_string += html_code

    return html_string, variable_locations


def sequence_consensus(sequence_list, vir_typer_pk):
    from Bio.Align import AlignInfo
    from Bio.SeqRecord import SeqRecord
    from Bio.Alphabet import IUPAC
    from Bio import AlignIO, SeqIO
    from Bio.Seq import Seq
    consensus = list()
    records = list()
    if len(sequence_list) >= 2:
        for sequence_dict in sequence_list:
            # print(sequence_dict)
            for seq_code, sequence in sequence_dict.items():
                record = SeqRecord(Seq(sequence, IUPAC.unambiguous_dna),
                                   id=seq_code,
                                   description='')
                records.append(record)
        file_path = 'olc_webportalv2/media/vir_typer/{pk}/'.format(pk=vir_typer_pk)
        try:
            os.makedirs(file_path)
        except FileExistsError:
            pass
        with open(file_path + 'alignment.fasta', 'w') as alignment_file:
            SeqIO.write(records, alignment_file, 'fasta')
        with open(file_path + 'alignment.fasta', 'r') as alignment_file:
            alignment = AlignIO.read(alignment_file, 'fasta')
        summary_align = AlignInfo.SummaryInfo(alignment)
        consensus = summary_align.dumb_consensus()
    else:
        for sequence_dict in sequence_list:
            # print(sequence_dict)
            for seq_code, sequence in sequence_dict.items():
                consensus = sequence
    return consensus


@csrf_exempt
@login_required
def vir_typer_results(request, vir_typer_pk):
    from django.core.files.storage import FileSystemStorage
    from django.http import HttpResponse
    from django.template.loader import render_to_string
    import datetime
    manager_codes = {
        'NOVI': {
            'code': 'NOV1-SEQ',
            'comments_en': 'Norovirus genogroup I was confirmed by gene sequencing of the amplicon.',
            'comments_fr': 'Le séquençage de l\'amplicon confirme la présence du Norovirus génogroup I.'

        },
        'NOVII': {
            'code': 'NOV2-SEQ',
            'comments_en': 'Norovirus genogroup II was confirmed by gene sequencing of the amplicon.',
            'comments_fr': 'Le séquençage de l\'amplicon confirme la présence du Norovirus génogroup II.'
        },
        'HAV': {
            'code': 'HAV-SEQ',
            'comments_en': 'Hepatitis A was confirmed by gene sequencing of the amplicon.',
            'comments_fr': 'Le séquençage de l\'amplicon confirme la présence du Hepatitis A.'
        }
    }
    vir_typer_project = get_object_or_404(VirTyperProject, pk=vir_typer_pk)

    vir_typer_samples = list(VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk))
    vir_typer_result = list()
    parse = False
    for sample in vir_typer_samples:
        vir_files = list(VirTyperFiles.objects.filter(sample_name__pk=sample.pk))
        for vir_file in vir_files:
            try:
                vir_typer_result.append(VirTyperResults.objects.get(sequence_file__pk=vir_file.pk))
            except:
                parse = True
    # Parse the JSON report, and enter the results into the database if necessary
    if parse:
        parse_report(vir_typer_json=vir_typer_project.report,
                     vir_typer_samples=vir_typer_samples)
    vir_typer_samples = list(VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk))
    pn = vir_typer_project.project_name
    vir_files = list()
    full_results = dict()
    full_results['data'] = list()
    codes = set()
    alleles = dict()
    outputs = list()
    samples = list()
    for sample in VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk):
        samples.append(sample.sample_name)
    for sorted_sample in sorted(samples):
        for sample in VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk):
            if sample.sample_name == sorted_sample:
                sequences = list()
                sample_dict = dict()
                sample_dict['sample_project'] = pn
                sample_dict['sample_name'] = sample.sample_name
                sample_dict['lsts'] = sample.LSTS_ID
                sample_dict['date_received'] = '{:%Y-%m-%d}'.format(sample.date_received)
                sample_dict['isolate_source'] = sample.isolate_source
                sample_dict['organism'] = sample.putative_classification
                try:
                    codes.add((sample.putative_classification,
                               manager_codes[sample.putative_classification]['code'],
                               manager_codes[sample.putative_classification]['comments_en'],
                               manager_codes[sample.putative_classification]['comments_fr']))
                except KeyError:
                    pass
                sample_dict['identifier'] = list()
                sample_dict['allele'] = list()
                sample_dict['sequence'] = list()
                sample_dict['sequence_length'] = list()
                sample_dict['variable_locations'] = list()
                sample_dict['mean_quality'] = list()
                sample_dict['stdev_quality'] = list()
                alleles[sample.sample_name] = set()
                for vir_file in VirTyperFiles.objects.filter(sample_name__pk=sample.pk):
                    vir_files.append(vir_file)
                    seq_identifier_well = str(os.path.splitext(vir_file.sequence_file)[0].split('_')[-2])
                    seq_identifier_num = os.path.splitext(vir_file.sequence_file)[0].split('_')[-1]
                    seq_identifier_code = '_'.join((seq_identifier_well, seq_identifier_num))
                    sample_dict['identifier'].append(seq_identifier_code + '\n')
                    result = VirTyperResults.objects.filter(sequence_file__pk=vir_file.pk)
                    for vir_typer_result in result:
                        sample_dict['allele'].append(vir_typer_result.allele + '\n')
                        alleles[sample.sample_name].add(vir_typer_result.allele)
                        sequences.append({sample.sample_name + '_' + seq_identifier_code: vir_typer_result
                                         .trimmed_sequence})
                        sample_dict['sequence_length'].append(vir_typer_result.trimmed_sequence_len + '\n')
                        sample_dict['mean_quality'].append(vir_typer_result.trimmed_quality_mean)
                        sample_dict['stdev_quality'].append(vir_typer_result.trimmed_quality_stdev)

                consensus_sequence = sequence_consensus(sequences, vir_typer_pk)
                for vir_file in VirTyperFiles.objects.filter(sample_name__pk=sample.pk):
                    result = VirTyperResults.objects.filter(sequence_file__pk=vir_file.pk)
                    for vir_typer_result in result:
                        html_string, variable_locations = sequence_html_string(vir_typer_result.trimmed_sequence,
                                                                               consensus_sequence)
                        sample_dict['variable_locations'].append(variable_locations)
                        # print(sample.sample_name, vir_file, variable_locations)
                        sample_dict['sequence'].append(html_string + '\n')
                        # print(html_string)
                full_results['data'].append(sample_dict)
                outputs.append(sample_dict)
    json_path = 'olc_webportalv2/static/ajax/vir_typer/{pk}/arrays.txt'.format(pk=vir_typer_pk)
    data_tables_path = '../../../../static/ajax/vir_typer/{pk}/arrays.txt'.format(pk=vir_typer_pk)
    os.makedirs('olc_webportalv2/static/ajax/vir_typer/{pk}'.format(pk=vir_typer_pk), exist_ok=True)
    # Create the JSON-formatted output file
    with open(json_path, 'w') as json_out:
        json.dump(full_results, json_out)
    if request.method == 'POST':
        # Create a string of the HTML output using the appropriate template and variables
        html_string = render_to_string('vir_typer/vir_typer_results_to_pdf.html',
                                       {
                                           'vir_typer_project': vir_typer_project,
                                           'date': '{:%Y-%m-%d}'.format(datetime.datetime.now()),
                                           'codes': sorted(list(codes)),
                                           'results': outputs,
                                           'vir_typer_samples': vir_typer_samples,
                                       })
        # Create an HTML object from the HTML string
        html = HTML(string=html_string, base_url=request.build_absolute_uri())
        # Set the links to the CSS files
        bootstrap_css = CSS(filename='olc_webportalv2/static/css/bootstrap.min.css')
        project_css = CSS(filename='olc_webportalv2/static/css/project.css')
        all_css = CSS(filename='olc_webportalv2/static/fonts/css/all.css')
        # Create a custom CSS string to make the page letter sized, with landscape orientation
        page_css = CSS(string='@page { size: Letter landscape; margin: 1cm }')
        #
        html.write_pdf(target='olc_webportalv2/media/vir_typer/{pk}/VirusTyperReport.pdf'.format(pk=vir_typer_pk),
                       stylesheets=[bootstrap_css, project_css, all_css, page_css])
        # Download
        fs = FileSystemStorage('olc_webportalv2/media/vir_typer/{pk}/'.format(pk=vir_typer_pk))
        with fs.open("VirusTyperReport.pdf") as pdf:
            response = HttpResponse(pdf, content_type='application/pdf')
            response['Content-Disposition'] = 'attachment; filename="VirusTyperReport.pdf"'
            return response
    return render(request,
                  'vir_typer/vir_typer_results.html',
                  {
                      'vir_typer_project': vir_typer_project,
                      'date': '{:%Y-%m-%d}'.format(datetime.datetime.now()),
                      'codes': sorted(list(codes)),
                      'json_results': data_tables_path,
                      'results': outputs,
                      'vir_typer_samples': vir_typer_samples,
                      'vir_typer_files': vir_files,
                  })


@login_required
def vir_typer_rename(request, vir_typer_pk):
    vir_typer_project = get_object_or_404(VirTyperProject, pk=vir_typer_pk)
    tombstone_form = VirTyperProjectForm(instance=vir_typer_project)
    # vir_typer_project = get_object_or_404(VirTyperProject, pk=vir_typer_pk)
    if request.method == "POST":
        tombstone_form = VirTyperProjectForm(request.POST, instance=vir_typer_project)
        if tombstone_form.is_valid():
            # vir_typer_project.project_name = form.cleaned_data['project_name']
            # vir_typer_project.save()
            tombstone_form.save()
            messages.success(request,
                             _('Project %s updated')% vir_typer_project.project_name)
            return redirect('vir_typer:vir_typer_home')
        else:
            messages.error(request, tombstone_form.errors.as_json())

    return render(request,
                  'vir_typer/vir_typer_edit_project.html',
                  {
                      'vir_typer_project': vir_typer_project,
                      'tombstone_form': tombstone_form
                  })
