"""Views for the COWSNPhR analysis workflows."""

# Standard imports

# Django
# Third-party imports
from dal import autocomplete
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

# Portal-specific imports
from olc_webportalv2.cowsnphr.forms import (
    ContainerForm,
    COWSNPhrForm,
    COWSNPhrSEQIDForm,
)
from olc_webportalv2.cowsnphr.models import ContainerName, COWSNPhRRequest
from olc_webportalv2.cowsnphr.tasks import (
    refresh_container_names,
    run_cowsnphr_batch,
    upload_files_for_cowsnphr,
)
from olc_webportalv2.geneseekr.forms import EmailForm


@csrf_exempt  # needed or IE explodes
@login_required
def cowsnphr_home(request):
    """Render the home page with the current user's analysis requests.

    Args:
        request: Authenticated HTTP request.

    """
    cowsnphr_projects = COWSNPhRRequest.objects.filter(user=request.user)
    if request.method == "POST":
        if request.POST.get("delete"):
            query = COWSNPhRRequest.objects.filter(
                pk=request.POST.get("delete")
            )
            query.delete()

    return render(
        request, "cowsnphr/cowsnphr_home.html", {"projects": cowsnphr_projects}
    )


@csrf_exempt  # needed or IE explodes
@login_required
def blob_cowsnphr(request):
    """Create an analysis using files already in blob storage.

    Args:
        request: Authenticated HTTP request.

    """
    # Create the analysis form and container autocomplete form.
    cowsnphr_form = COWSNPhrForm()
    container_form = ContainerForm()
    # Track whether sequence files are already in blob storage.
    upload = False
    # Perform actions on a POST
    if request.method == "POST":
        cowsnphr_form = COWSNPhrForm(request.POST, request.FILES)
        if cowsnphr_form.is_valid():
            # Populate the request without committing; the user must be added
            # before saving.
            cowsnphr = cowsnphr_form.save(commit=False)
            cowsnphr.user = request.user
            # Update the status to processing
            cowsnphr.status = "Processing"
            cowsnphr.save()
            # Use the primary key if no project name was provided.
            if not cowsnphr.project_name:
                cowsnphr.project_name = str(cowsnphr)
            cowsnphr.save()

            # Create and submit the batch job
            run_cowsnphr_batch.apply_async(
                queue="cowbat", args=(cowsnphr.pk,), countdown=10
            )
            # Redirect the view to cowsnphr_processing
            return redirect(
                "cowsnphr:cowsnphr_processing", cowsnphr_pk=cowsnphr.pk
            )

    return render(
        request,
        "cowsnphr/cowsnphr_create.html",
        {
            "cowsnphr_form": cowsnphr_form,
            "container_form": container_form,
            "upload": upload,
        },
    )


@csrf_exempt  # needed or IE explodes
@login_required
def upload_cowsnphr(request):
    """Create an analysis that requires sequence-file uploads.

    Args:
        request: Authenticated HTTP request.

    """
    # Create forms for the COWSNPhRRequest model
    cowsnphr_form = COWSNPhrForm()
    upload = True
    # Perform actions on a POST
    if request.method == "POST":
        cowsnphr_form = COWSNPhrForm(request.POST, request.FILES, upload=True)
        if cowsnphr_form.is_valid():
            # Populate the request without committing; the user must be added
            # before saving.
            cowsnphr = cowsnphr_form.save(commit=False)
            cowsnphr.user = request.user
            cowsnphr.save()
            # Use the primary key if no project name was provided.
            if not cowsnphr.project_name:
                cowsnphr.project_name = str(cowsnphr)
            # Also set the container name
            cowsnphr.container_name = str(cowsnphr)
            cowsnphr.save()
            # Redirect the view to the sequence upload page
            return redirect(
                "cowsnphr:upload_cowsnphr_files", cowsnphr_pk=cowsnphr.pk
            )

    return render(
        request,
        "cowsnphr/cowsnphr_create.html",
        {"cowsnphr_form": cowsnphr_form, "upload": upload},
    )


@csrf_exempt  # needed or IE explodes
@login_required
def seqid_cowsnphr(request):
    """Create an analysis from query and reference SEQIDs.

    Args:
        request: Authenticated HTTP request.

    """
    # Create the analysis form and container autocomplete form.
    cowsnphr_form = COWSNPhrSEQIDForm()
    # Perform actions on a POST
    if request.method == "POST":
        cowsnphr_form = COWSNPhrSEQIDForm(request.POST)
        if cowsnphr_form.is_valid():
            cowsnphr = cowsnphr_form.save(commit=False)
            cowsnphr.user = request.user
            cowsnphr.seqids = cowsnphr_form.cleaned_data.get("seqids")
            cowsnphr.ref = cowsnphr_form.cleaned_data.get("ref")
            cowsnphr.save()
            # Use the primary key if no project name was provided.
            if not cowsnphr_form.cleaned_data.get("project_name"):
                cowsnphr.project_name = str(cowsnphr)
            # Set the name of the container
            cowsnphr.container_name = str(cowsnphr)
            cowsnphr.save()
            # Redirect the view to cowsnphr_processing
            return redirect(
                "cowsnphr:cowsnphr_processing", cowsnphr_pk=cowsnphr.pk
            )
    return render(
        request,
        "cowsnphr/cowsnphr_seqid.html",
        {
            "cowsnphr_form": cowsnphr_form,
        },
    )


@csrf_exempt  # needed or IE explodes
@login_required
def upload_cowsnphr_files(request, cowsnphr_pk):
    """Upload sequence files to the request's blob container.

    Args:
        request: Authenticated HTTP request.
        cowsnphr_pk: Primary key of the analysis request.

    """
    # Extract the COWSNPhR model using the supplied pk
    cowsnphr = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    if request.method == "POST":
        for _, file in request.FILES.items():
            upload_files_for_cowsnphr(
                file=file, container_name=cowsnphr.container_name
            )

    return render(
        request,
        "cowsnphr/cowsnphr_upload.html",
        {
            "cowsnphr": cowsnphr,
        },
    )


def container_refresh(_request):
    """Refresh containers shown by the autocomplete widget.

    Args:
        _request: HTTP request that triggered the refresh.

    """
    # Refresh the database entries of the container names
    refresh_container_names()
    return redirect("cowsnphr:blob_cowsnphr")


# Has to be its own view; can't be mixed with others
class COWSNPhRAutoCompleter(autocomplete.Select2QuerySetView):
    """Provide container-name suggestions for COWSNPhR forms."""

    def get_queryset(self):
        """Return containers matching the current autocomplete query."""
        query_set = ContainerName.objects.all()
        if self.q:
            query_set = query_set.filter(container_name__icontains=self.q)
        return query_set.order_by("container_name", "pk").distinct(
            "container_name"
        )


@csrf_exempt  # needed or IE explodes
@login_required
def cowsnphr_processing(request, cowsnphr_pk):
    """Render request status and accept completion-notification emails.

    Args:
        request: Authenticated HTTP request.
        cowsnphr_pk: Primary key of the analysis request.

    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    # Submit a batch job when sequence files need to be uploaded.
    if cowsnphr_request.status == "Unprocessed":
        cowsnphr_request.status = "Processing"
        cowsnphr_request.save()
        # Create and submit the batch job
        run_cowsnphr_batch.apply_async(
            queue="cowbat", args=(cowsnphr_pk,), countdown=10
        )
    form = EmailForm()
    if request.method == "POST":
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            emails = cowsnphr_request.emails_array or []
            if email not in emails:
                emails.append(email)
                cowsnphr_request.emails_array = emails
                cowsnphr_request.save()
                form = EmailForm()
                messages.success(request, _("Email saved"))
    return render(
        request,
        "cowsnphr/cowsnphr_processing.html",
        {"request": cowsnphr_request, "form": form},
    )


def cowsnphr_reports(request, cowsnphr_pk):
    """Render the report index for an analysis.

    Args:
        request: HTTP request.
        cowsnphr_pk: Primary key of the analysis request.

    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    return render(
        request,
        "cowsnphr/cowsnphr_reports.html",
        {
            "request": cowsnphr_request,
        },
    )


def cowsnphr_nucleotide_summary(request, cowsnphr_pk):
    """Render the nucleotide summary table.

    Args:
        request: HTTP request.
        cowsnphr_pk: Primary key of the analysis request.

    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    return render(
        request,
        "cowsnphr/cowsnphr_nucleotide_summary.html",
        {
            "request": cowsnphr_request,
        },
    )


def cowsnphr_amino_acid_summary(request, cowsnphr_pk):
    """Render the amino-acid summary table.

    Args:
        request: HTTP request.
        cowsnphr_pk: Primary key of the analysis request.

    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    return render(
        request,
        "cowsnphr/cowsnphr_amino_acid_summary.html",
        {
            "request": cowsnphr_request,
        },
    )


def cowsnphr_tree(request, cowsnphr_pk):
    """Render the phylogenetic tree for an analysis.

    Args:
        request: HTTP request.
        cowsnphr_pk: Primary key of the analysis request.

    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    return render(
        request,
        "cowsnphr/cowsnphr_tree.html",
        {
            "request": cowsnphr_request,
        },
    )
