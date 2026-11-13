"""Views and report helpers for VirusTyper workflows."""

import datetime
import json
import os
from html import escape

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.forms.formsets import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from olc_webportalv2.common.storage_client import get_storage_client

from .forms import (
    BaseVirTyperSampleFormSet,
    VirTyperProjectForm,
    VirTyperSampleForm,
)
from .models import (
    VirTyperFiles,
    VirTyperProject,
    VirTyperRequest,
    VirTyperResults,
)
from .tasks import run_vir_typer


@login_required
def vir_typer_home(request):
    """Display the current user's VirusTyper projects.

    Args:
        request: Incoming HTTP request.

    """
    vir_typer_projects = VirTyperProject.objects.filter(user=request.user)

    if request.method == "POST":
        if request.POST.get("delete"):
            VirTyperProject.objects.filter(
                pk=request.POST.get("delete"),
                user=request.user,
            ).delete()

    return render(
        request,
        "vir_typer/vir_typer_home.html",
        {"vir_typer_projects": vir_typer_projects},
    )


@login_required
def vir_typer_request(request):
    """Create a project and its sample requests from submitted forms.

    Args:
        request: Incoming HTTP request.

    """
    tombstone_form = VirTyperProjectForm()
    # Create a sample form set using the sample form and formset_factory
    sample_form_set_factory = formset_factory(
        VirTyperSampleForm, formset=BaseVirTyperSampleFormSet, max_num=10
    )
    if request.method == "POST":
        tombstone_form = VirTyperProjectForm(request.POST)
        sample_form_set = sample_form_set_factory(request.POST)
        if tombstone_form.is_valid() and sample_form_set.is_valid():
            project_name = tombstone_form.cleaned_data.get("project_name")
            try:
                vir_typer_project = VirTyperProject.objects.create(
                    project_name=project_name, user=request.user
                )

                sample_data = list()
                for form in sample_form_set:
                    sample_name = form.cleaned_data.get("sample_name")
                    date_received = form.cleaned_data.get("date_received")
                    lab_id = form.cleaned_data.get("lab_ID")
                    lsts_id = form.cleaned_data.get("LSTS_ID")
                    isolate_source = form.cleaned_data.get("isolate_source")
                    putative_classification = form.cleaned_data.get(
                        "putative_classification"
                    )
                    analyst_name = form.cleaned_data.get("analyst_name")
                    # Default the optional subunit to zero when it is omitted.
                    subunit = (
                        0
                        if form.cleaned_data["subunit"] is None
                        else form.cleaned_data.get("subunit")
                    )
                    # Save a sample only when all required fields are present.
                    if (
                        sample_name
                        and date_received
                        and lab_id
                        and lsts_id
                        and isolate_source
                        and putative_classification
                        and analyst_name
                    ):
                        sample_data.append(
                            VirTyperRequest(
                                sample_name=sample_name,
                                project_name_id=vir_typer_project.pk,
                                date_received=date_received,
                                lab_ID=lab_id,
                                LSTS_ID=lsts_id,
                                isolate_source=isolate_source,
                                putative_classification=putative_classification,
                                analyst_name=analyst_name,
                                subunit=subunit,
                            )
                        )

                if sample_data:
                    vir_typer_project.save()
                    try:
                        VirTyperRequest.objects.bulk_create(sample_data)
                        message_str = _("You have created project ")
                        messages.success(
                            request,
                            message_str + f"{vir_typer_project.project_name}",
                        )

                        return redirect(
                            "vir_typer:vir_typer_upload",
                            vir_typer_pk=vir_typer_project.pk,
                        )
                    # Database constraints enforce unique names and LSTS IDs.
                    except IntegrityError:
                        out_str = sample_form_set.non_form_errors()
                        messages.error(request, out_str)
            # Project names must be unique for each user.
            except IntegrityError:
                messages.error(request, _("Project Name must be unique"))

        # If forms aren't valid, error messages
        else:
            out_str = str()
            if tombstone_form.cleaned_data.get("project_name") is None:
                out_str += tombstone_form.errors["project_name"]
            out_str += sample_form_set.non_form_errors()
            messages.error(request, out_str)
    else:
        sample_form_set = sample_form_set_factory()
    return render(
        request,
        "vir_typer/vir_typer_create.html",
        {
            "tombstone_form": tombstone_form,
            "sample_formset": sample_form_set,
        },
    )


@login_required
def vir_typer_upload(request, vir_typer_pk):
    """Accept sequence files for a project owned by the current user.

    Args:
        request: Incoming HTTP request.
        vir_typer_pk: Primary key of the project to update.

    """
    vir_typer_project = get_object_or_404(
        VirTyperProject,
        pk=vir_typer_pk,
        user=request.user,
    )
    vir_typer_samples = list(
        VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk)
    )
    sample_names = list()
    for sample in vir_typer_samples:
        sample_names.append(str(sample.LSTS_ID))
    if request.method == "POST":
        seq_files = [
            request.FILES.get("file[%d]" % i)
            for i in range(0, len(request.FILES))
        ]
        if seq_files:
            container_name = vir_typer_project.container_namer()
            storage = get_storage_client()
            storage.ensure_container(container_name)
            for item in seq_files:
                storage.upload_bytes(
                    container_name,
                    item.name,
                    item.read(),
                    content_type=getattr(item, "content_type", None),
                )
            for sample in vir_typer_samples:
                for seq_file in seq_files:
                    if str(sample.LSTS_ID) in str(seq_file):
                        vir_files = VirTyperFiles(
                            sample_name_id=sample.pk,
                            sequence_file=seq_file.name,
                        )
                        vir_files.save()
            vir_typer_project.status = "Processing"
            vir_typer_project.save()
            run_vir_typer.apply_async(
                queue="cowbat", args=(vir_typer_pk,), countdown=10
            )
        return redirect("vir_typer:vir_typer_home")
    return render(
        request,
        "vir_typer/vir_typer_upload_sequences.html",
        {
            "vir_typer_project": vir_typer_project,
            "vir_typer_samples": vir_typer_samples,
            "vir_typer_sample_names": sample_names,
        },
    )


def parse_report(vir_typer_json, vir_typer_samples):
    """Parse a report and save available results for each uploaded file.

    Args:
        vir_typer_json: JSON or Python-literal-like report string.
        vir_typer_samples: Sample requests whose files should be processed.

    """
    vir_typer_json = vir_typer_json.replace("'", '"')
    json_data = json.loads(vir_typer_json)
    for sample in vir_typer_samples:
        vir_files = list(
            VirTyperFiles.objects.filter(sample_name__pk=sample.pk)
        )
        for vir_file in vir_files:
            vir_sample = os.path.splitext(vir_file.sequence_file)[0]
            try:
                file_dict = json_data[vir_sample]
                vir_results = VirTyperResults(
                    sequence_file_id=vir_file.id,
                    allele=file_dict["allele"],
                    orientation=file_dict["orientation"],
                    forward_primer=[
                        key for key in file_dict["primer_matches"]["forward"]
                    ][0],
                    reverse_primer=[
                        key for key in file_dict["primer_matches"]["reverse"]
                    ][0],
                    trimmed_sequence=file_dict["trimmed_seq"],
                    trimmed_sequence_len=len(file_dict["trimmed_seq"]),
                    trimmed_quality_max=file_dict["trimmed_quality_max"],
                    trimmed_quality_mean=file_dict["trimmed_quality_mean"],
                    trimmed_quality_min=file_dict["trimmed_quality_min"],
                    trimmed_quality_stdev=file_dict["trimmed_quality_stdev"],
                )
                vir_results.save()
            except KeyError:
                pass


def sequence_html_string(sequence, consensus_sequence):
    """Render a sequence with consensus matches and variation highlighted.

    Args:
        sequence: Nucleotide sequence to render.
        consensus_sequence: Consensus nucleotide sequence for comparison.

    Returns:
        Rendered HTML and the number of variable sites.

    Raises:
        ValueError: If the sequence and consensus lengths differ.

    """
    if len(sequence) != len(consensus_sequence):
        raise ValueError("Sequence and consensus sequence lengths must match.")

    consensus_colors = {
        "A": ("white", "green"),
        "C": ("black", "blue"),
        "G": ("white", "black"),
        "T": ("black", "red"),
    }
    variable_colors = {
        "A": ("green", "white"),
        "C": ("blue", "white"),
        "G": ("black", "white"),
        "T": ("red", "white"),
    }
    html_string = str()
    variable_locations = 0
    for nt, consensus_nt in zip(sequence, consensus_sequence):
        if consensus_nt.upper() != "X":
            color_map = consensus_colors
        else:
            variable_locations += 1
            color_map = variable_colors

        colors = color_map.get(nt.upper())
        if colors is None:
            html_code = (
                "<span style='color:purple;background-color:white;"
                f"font-family:courier;'>{escape(nt.upper())}</span>"
            )
        else:
            foreground, background = colors
            html_code = (
                f"<span style='color:{foreground};"
                f"background-color:{background};font-family:courier;'>"
                f"{nt.upper()}</span>"
            )
        html_string += html_code

    return html_string, variable_locations


def sequence_consensus(sequence_list, vir_typer_pk):
    """Calculate a majority-rule consensus for a sample's sequences.

    Args:
        sequence_list: Sequence dictionaries to align and compare.
        vir_typer_pk: Project ID used for temporary alignment files.

    Returns:
        The consensus sequence, with ``X`` marking variable positions.

    """
    from collections import Counter

    from Bio import AlignIO, SeqIO
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord

    consensus = list()
    records = list()
    if len(sequence_list) >= 2:
        for sequence_dict in sequence_list:
            # print(sequence_dict)
            for seq_code, sequence in sequence_dict.items():
                record = SeqRecord(Seq(sequence), id=seq_code, description="")
                records.append(record)
        file_path = f"olc_webportalv2/media/vir_typer/{vir_typer_pk}/"
        os.makedirs(file_path, exist_ok=True)
        with open(file_path + "alignment.fasta", "w") as alignment_file:
            SeqIO.write(records, alignment_file, "fasta")
        with open(file_path + "alignment.fasta", "r") as alignment_file:
            alignment = AlignIO.read(alignment_file, "fasta")
        for column in range(alignment.get_alignment_length()):
            counts = Counter(str(record.seq[column]) for record in alignment)
            base, count = counts.most_common(1)[0]
            consensus.append(base if count / len(alignment) >= 0.7 else "X")
        consensus = "".join(consensus)
    else:
        for sequence_dict in sequence_list:
            # print(sequence_dict)
            for seq_code, sequence in sequence_dict.items():
                consensus = sequence
    return consensus


@login_required
def vir_typer_results(request, vir_typer_pk):
    """Display or download the report for the current user's project.

    Args:
        request: Incoming HTTP request.
        vir_typer_pk: Primary key of the project to display.

    """
    from django.core.files.storage import FileSystemStorage
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    manager_codes = {
        "NOVI": {
            "code": "NOV1-SEQ",
            "comments_en": (
                "Norovirus genogroup I was confirmed by gene sequencing "
                "of the amplicon."
            ),
            "comments_fr": (
                "Le séquençage de l'amplicon confirme la présence du "
                "Norovirus génogroup I."
            ),
        },
        "NOVII": {
            "code": "NOV2-SEQ",
            "comments_en": (
                "Norovirus genogroup II was confirmed by gene sequencing "
                "of the amplicon."
            ),
            "comments_fr": (
                "Le séquençage de l'amplicon confirme la présence du "
                "Norovirus génogroup II."
            ),
        },
        "HAV": {
            "code": "HAV-SEQ",
            "comments_en": (
                "Hepatitis A was confirmed by gene sequencing of the amplicon."
            ),
            "comments_fr": (
                "Le séquençage de l'amplicon confirme la présence du "
                "Hepatitis A."
            ),
        },
        "MNV": {
            "code": "MNV-SEQ",
            "comments_en": (
                "Murine norovirus was confirmed by gene sequencing of the "
                "amplicon."
            ),
            "comments_fr": (
                "Le séquençage de l'amplicon confirme la présence du Murine "
                "norovirus."
            ),
        },
    }
    vir_typer_project = get_object_or_404(
        VirTyperProject,
        pk=vir_typer_pk,
        user=request.user,
    )

    vir_typer_samples = list(
        VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk)
    )
    vir_typer_result = list()
    parse = False
    for sample in vir_typer_samples:
        vir_files = list(
            VirTyperFiles.objects.filter(sample_name__pk=sample.pk)
        )
        for vir_file in vir_files:
            try:
                vir_typer_result.append(
                    VirTyperResults.objects.get(sequence_file__pk=vir_file.pk)
                )
            except VirTyperResults.DoesNotExist:
                parse = True
    # Parse the report into the database when results have not been saved.
    if parse:
        parse_report(
            vir_typer_json=vir_typer_project.report,
            vir_typer_samples=vir_typer_samples,
        )
    vir_typer_samples = list(
        VirTyperRequest.objects.filter(project_name__pk=vir_typer_pk)
    )
    pn = vir_typer_project.project_name
    vir_files = list()
    full_results = dict()
    full_results["data"] = list()
    codes = set()
    alleles = dict()
    outputs = list()
    samples = list()
    for sample in VirTyperRequest.objects.filter(
        project_name__pk=vir_typer_pk
    ):
        samples.append(sample.sample_name)
    for sorted_sample in sorted(samples):
        for sample in VirTyperRequest.objects.filter(
            project_name__pk=vir_typer_pk
        ):
            if sample.sample_name == sorted_sample:
                sequences = list()
                sample_dict = dict()
                sample_dict["sample_project"] = pn
                sample_dict["sample_name"] = sample.sample_name
                sample_dict["lsts"] = sample.LSTS_ID
                sample_dict["date_received"] = (
                    f"{sample.date_received:%Y-%m-%d}"
                )
                sample_dict["isolate_source"] = sample.isolate_source
                sample_dict["organism"] = sample.putative_classification
                try:
                    codes.add(
                        (
                            sample.putative_classification,
                            manager_codes[sample.putative_classification][
                                "code"
                            ],
                            manager_codes[sample.putative_classification][
                                "comments_en"
                            ],
                            manager_codes[sample.putative_classification][
                                "comments_fr"
                            ],
                        )
                    )
                except KeyError:
                    pass
                sample_dict["identifier"] = list()
                sample_dict["allele"] = list()
                sample_dict["sequence"] = list()
                sample_dict["sequence_length"] = list()
                sample_dict["variable_locations"] = list()
                sample_dict["mean_quality"] = list()
                sample_dict["stdev_quality"] = list()
                alleles[sample.sample_name] = set()
                for vir_file in VirTyperFiles.objects.filter(
                    sample_name__pk=sample.pk
                ):
                    vir_files.append(vir_file)
                    seq_identifier_well = str(
                        os.path.splitext(vir_file.sequence_file)[0].split("_")[
                            -2
                        ]
                    )
                    seq_identifier_num = os.path.splitext(
                        vir_file.sequence_file
                    )[0].split("_")[-1]
                    seq_identifier_code = "_".join(
                        (seq_identifier_well, seq_identifier_num)
                    )
                    sample_dict["identifier"].append(
                        seq_identifier_code + "\n"
                    )
                    result = VirTyperResults.objects.filter(
                        sequence_file__pk=vir_file.pk
                    )
                    for vir_typer_result in result:
                        sample_dict["allele"].append(
                            vir_typer_result.allele + "\n"
                        )
                        alleles[sample.sample_name].add(
                            vir_typer_result.allele
                        )
                        sequence_key = (
                            f"{sample.sample_name}_{seq_identifier_code}"
                        )
                        sequences.append(
                            {sequence_key: (vir_typer_result.trimmed_sequence)}
                        )
                        sample_dict["sequence_length"].append(
                            vir_typer_result.trimmed_sequence_len + "\n"
                        )
                        sample_dict["mean_quality"].append(
                            vir_typer_result.trimmed_quality_mean
                        )
                        sample_dict["stdev_quality"].append(
                            vir_typer_result.trimmed_quality_stdev
                        )

                consensus_sequence = sequence_consensus(
                    sequences, vir_typer_pk
                )
                for vir_file in VirTyperFiles.objects.filter(
                    sample_name__pk=sample.pk
                ):
                    result = VirTyperResults.objects.filter(
                        sequence_file__pk=vir_file.pk
                    )
                    for vir_typer_result in result:
                        html_string, variable_locations = sequence_html_string(
                            vir_typer_result.trimmed_sequence,
                            consensus_sequence,
                        )
                        sample_dict["variable_locations"].append(
                            variable_locations
                        )
                        sample_dict["sequence"].append(html_string + "\n")
                full_results["data"].append(sample_dict)
                outputs.append(sample_dict)
    json_path = (
        f"olc_webportalv2/static/ajax/vir_typer/{vir_typer_pk}/arrays.txt"
    )
    data_tables_path = (
        f"../../../../static/ajax/vir_typer/{vir_typer_pk}/arrays.txt"
    )
    os.makedirs(
        f"olc_webportalv2/static/ajax/vir_typer/{vir_typer_pk}", exist_ok=True
    )
    os.makedirs(
        f"olc_webportalv2/media/vir_typer/{vir_typer_pk}", exist_ok=True
    )
    # Create the JSON-formatted output file
    with open(json_path, "w") as json_out:
        json.dump(full_results, json_out)
    if request.method == "POST":
        # Render the HTML report before converting it to a PDF.
        html_string = render_to_string(
            "vir_typer/vir_typer_results_to_pdf.html",
            {
                "vir_typer_project": vir_typer_project,
                "date": f"{datetime.datetime.now():%Y-%m-%d}",
                "codes": sorted(list(codes)),
                "results": outputs,
                "vir_typer_samples": vir_typer_samples,
            },
        )
        fs = FileSystemStorage(
            f"olc_webportalv2/media/vir_typer/{vir_typer_pk}/"
        )
        with fs.open(
            f"VirusTyperReport_{vir_typer_project.project_name}.pdf"
        ) as pdf:
            response = HttpResponse(pdf, content_type="application/pdf")
            filename = f"VirusTyperReport_{vir_typer_project.project_name}.pdf"
            response["Content-Disposition"] = (
                f'attachment; filename="{filename}"'
            )
            return response
    return render(
        request,
        "vir_typer/vir_typer_results.html",
        {
            "vir_typer_project": vir_typer_project,
            "date": f"{datetime.datetime.now():%Y-%m-%d}",
            "codes": sorted(list(codes)),
            "json_results": data_tables_path,
            "results": outputs,
            "vir_typer_samples": vir_typer_samples,
            "vir_typer_files": vir_files,
        },
    )


@login_required
def vir_typer_rename(request, vir_typer_pk):
    """Rename a project owned by the current user.

    Args:
        request: Incoming HTTP request.
        vir_typer_pk: Primary key of the project to rename.

    """
    vir_typer_project = get_object_or_404(
        VirTyperProject,
        pk=vir_typer_pk,
        user=request.user,
    )
    tombstone_form = VirTyperProjectForm(instance=vir_typer_project)
    if request.method == "POST":
        tombstone_form = VirTyperProjectForm(
            request.POST, instance=vir_typer_project
        )
        if tombstone_form.is_valid():
            try:
                tombstone_form.save()
                message = (
                    f"{_('Project')} {vir_typer_project.project_name} "
                    f"{_('updated')}"
                )
                messages.success(request, message)
                return redirect("vir_typer:vir_typer_home")
            # Project names must be unique for each user.
            except IntegrityError:
                messages.error(request, _("Project Name must be unique"))
        else:
            messages.error(request, tombstone_form.errors.as_json())

    return render(
        request,
        "vir_typer/vir_typer_edit_project.html",
        {
            "vir_typer_project": vir_typer_project,
            "tombstone_form": tombstone_form,
        },
    )
