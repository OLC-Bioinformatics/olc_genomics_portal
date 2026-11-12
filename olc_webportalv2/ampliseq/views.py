"""Views for AmpliSeq analyses."""

# Standard imports
import os

# Third-party imports
from dal import autocomplete
from django.contrib import messages

# Django
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

# Portal-specific imports
from olc_webportalv2.ampliseq.forms import AmpliseqForm, ContainerForm
from olc_webportalv2.ampliseq.models import AmpliSeqRequest, ContainerName
from olc_webportalv2.ampliseq.tasks import (
    download_reports,
    refresh_container_names,
    run_ampliseq_batch,
    upload_file,
)
from olc_webportalv2.geneseekr.forms import EmailForm


@csrf_exempt  # Required for legacy Internet Explorer requests.
@login_required
def ampliseq_home(request):
    """List the current user's AmpliSeq analyses.

    Args:
        request: Incoming HTTP request.

    """
    ampliseq_projects = AmpliSeqRequest.objects.filter(user=request.user)
    if request.method == "POST":
        if request.POST.get("delete"):
            query = AmpliSeqRequest.objects.filter(
                pk=request.POST.get("delete")
            )
            query.delete()

    return render(
        request, "ampliseq/ampliseq_home.html", {"projects": ampliseq_projects}
    )


@csrf_exempt  # Required for legacy Internet Explorer requests.
@login_required
def blob_ampliseq(request):
    """Create an analysis using sequence files already in storage.

    Args:
        request: Incoming HTTP request.

    """
    # Prepare the analysis and container-selection forms.
    ampliseq_form = AmpliseqForm()
    container_form = ContainerForm()
    # These requests use files already in storage.
    upload = False
    # Perform actions on a POST
    if request.method == "POST":
        ampliseq_form = AmpliseqForm(request.POST, request.FILES)
        if ampliseq_form.is_valid():
            # Set the user before saving the request.
            ampliseq = ampliseq_form.save(commit=False)
            ampliseq.user = request.user
            # Update the status to processing
            ampliseq.status = "Processing"
            ampliseq.save()
            # Use the request identifier when no project name was supplied.
            if not ampliseq.project_name:
                ampliseq.project_name = str(ampliseq)
            ampliseq.save()
            # Upload the metadata file and classifier
            if ampliseq.metadata:
                ampliseq.metadata.file_name = request.FILES["metadata"]
                upload_file(
                    file=ampliseq.metadata.file_name,
                    container_name=ampliseq.container_name,
                )
                # Remove the temporary file from disk
                os.remove(ampliseq.metadata.path)

            # Upload the classifier file (if it was provided)
            if ampliseq.classifier:
                ampliseq.classifier.file_name = request.FILES["classifier"]
                upload_file(
                    file=ampliseq.classifier.file_name,
                    container_name=ampliseq.container_name,
                )
                os.remove(ampliseq.classifier.path)
            # Create and submit the batch job
            run_ampliseq_batch.apply_async(
                queue="cowbat", args=(ampliseq.pk,), countdown=10
            )
            # Redirect the view to ampliseq_processing
            return redirect(
                "ampliseq:ampliseq_processing", ampliseq_pk=ampliseq.pk
            )

    return render(
        request,
        "ampliseq/ampliseq_create.html",
        {
            "ampliseq_form": ampliseq_form,
            "container_form": container_form,
            "upload": upload,
        },
    )


def upload_ampliseq(request):
    """Create an analysis that requires sequence files to be uploaded.

    Args:
        request: Incoming HTTP request.

    """
    # Prepare the analysis and container-selection forms.
    ampliseq_form = AmpliseqForm()
    container_form = ContainerForm()
    # Set a variable to indicate that the sequence files are to be uploaded
    upload = True
    # Perform actions on a POST
    if request.method == "POST":
        ampliseq_form = AmpliseqForm(request.POST, request.FILES)
        ampliseq_form.upload = True
        if ampliseq_form.is_valid():
            # Set the user before saving the request.
            ampliseq = ampliseq_form.save(commit=False)
            ampliseq.user = request.user
            ampliseq.save()
            # Use the request identifier when no project name was supplied.
            if not ampliseq.project_name:
                ampliseq.project_name = str(ampliseq)
            # Also set the container name
            ampliseq.container_name = str(ampliseq)
            ampliseq.save()
            # Upload the metadata file and classifier
            if ampliseq.metadata:
                ampliseq.metadata.file_name = request.FILES["metadata"]
                upload_file(
                    file=ampliseq.metadata.file_name,
                    container_name=ampliseq.container_name,
                )
                # Remove the temporary file from disk
                os.remove(ampliseq.metadata.path)

            # Upload the classifier file (if it was provided)
            if ampliseq.classifier:
                ampliseq.classifier.file_name = request.FILES["classifier"]
                upload_file(
                    file=ampliseq.classifier.file_name,
                    container_name=ampliseq.container_name,
                )
                os.remove(ampliseq.classifier.path)
            # Redirect the view to the sequence upload page
            return redirect(
                "ampliseq:upload_ampliseq_files", ampliseq_pk=ampliseq.pk
            )

    return render(
        request,
        "ampliseq/ampliseq_create.html",
        {
            "ampliseq_form": ampliseq_form,
            "container_form": container_form,
            "upload": upload,
        },
    )


def upload_ampliseq_files(request, ampliseq_pk):
    """Upload sequence files for an AmpliSeq request.

    Args:
        request: Incoming HTTP request.
        ampliseq_pk: Primary key of the AmpliSeq request.

    """
    # Extract the AmpliSeq model using the supplied pk
    ampliseq = get_object_or_404(AmpliSeqRequest, pk=ampliseq_pk)
    if request.method == "POST":
        for _, file in request.FILES.items():
            upload_file(file=file, container_name=ampliseq.container_name)

    return render(
        request,
        "ampliseq/upload_sequence_data.html",
        {
            "ampliseq": ampliseq,
        },
    )


def container_refresh(request):
    """Refresh available storage containers.

    Args:
        request: Incoming HTTP request.

    """
    # Refresh the database entries of the container names
    refresh_container_names()
    return redirect("ampliseq:blob_ampliseq")


# Has to be its own view; can't be mixed with others
class AmpliSeqAutoCompleter(autocomplete.Select2QuerySetView):
    """Autocomplete storage containers available to AmpliSeq."""

    def get_queryset(self):
        """Return distinct container names for the selection field."""
        query_set = ContainerName.objects.all()
        if self.q:
            query_set = query_set.filter(container_name__icontains=self.q)
        return query_set.order_by("container_name", "pk").distinct(
            "container_name"
        )


@csrf_exempt  # Required for legacy Internet Explorer requests.
@login_required
def ampliseq_processing(request, ampliseq_pk):
    """Show processing status and allow email notifications.

    Args:
        request: Incoming HTTP request.
        ampliseq_pk: Primary key of the AmpliSeq request.

    """
    ampliseq_request = get_object_or_404(AmpliSeqRequest, pk=ampliseq_pk)
    # Submit requests that have not yet been processed.
    if ampliseq_request.status == "Unprocessed":
        ampliseq_request.status = "Processing"
        ampliseq_request.save()
        # Create and submit the batch job
        run_ampliseq_batch.apply_async(
            queue="cowbat", args=(ampliseq_pk,), countdown=10
        )
    form = EmailForm()
    if request.method == "POST":
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get("email")
            if email not in ampliseq_request.emails_array:
                ampliseq_request.emails_array.append(email)
                ampliseq_request.save()
                form = EmailForm()
                messages.success(request, _("Email saved"))
    return render(
        request,
        "ampliseq/ampliseq_processing.html",
        {"request": ampliseq_request, "form": form},
    )


@csrf_exempt  # Required for legacy Internet Explorer requests.
@login_required
def ampliseq_report(request, ampliseq_pk):
    """Display the AmpliSeq execution report.

    Args:
        request: Incoming HTTP request.
        ampliseq_pk: Primary key of the AmpliSeq request.

    """
    ampliseq_request = get_object_or_404(AmpliSeqRequest, pk=ampliseq_pk)
    report = (
        f"ampliseq/{ampliseq_request.container_name}_execution_report.html"
    )
    if not os.path.isfile(
        os.path.join("olc_webportalv2", "templates", report)
    ):
        download_reports(ampliseq=ampliseq_request)
    return render(request, report, {})


@csrf_exempt  # Required for legacy Internet Explorer requests.
@login_required
def ampliseq_timeline(request, ampliseq_pk):
    """Display the AmpliSeq execution timeline.

    Args:
        request: Incoming HTTP request.
        ampliseq_pk: Primary key of the AmpliSeq request.

    """
    ampliseq_request = get_object_or_404(AmpliSeqRequest, pk=ampliseq_pk)
    timeline = (
        f"ampliseq/{ampliseq_request.container_name}_execution_timeline.html"
    )
    if not os.path.isfile(
        os.path.join("olc_webportalv2", "templates", timeline)
    ):
        download_reports(ampliseq=ampliseq_request)
    return render(request, timeline, {})
