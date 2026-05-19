# Standard imports
import os

# Django
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render
)
try:
    from django.utils.translation import gettext_lazy as _
except ImportError:
    from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

# Third-party imports
from dal import autocomplete

# Portal-specific imports
from olc_webportalv2.ampliseq.tasks import (
    upload_file
)
from olc_webportalv2.cowsnphr.forms import (
    ContainerForm,
    COWSNPhrForm,
    COWSNPhrSEQIDForm
    )
from olc_webportalv2.cowsnphr.models import (
    ContainerName,
    COWSNPhRRequest
)
from olc_webportalv2.cowsnphr.tasks import (
    refresh_container_names,
    run_cowsnphr_batch,
    upload_files_for_cowsnphr
)
from olc_webportalv2.geneseekr.forms import EmailForm

@csrf_exempt  # needed or IE explodes
@login_required
def cowsnphr_home(request):
    """
    View for COWSNPhR homepage
    :param request: HttpRequest object
    """
    cowsnphr_projects = COWSNPhRRequest.objects.filter(user=request.user)
    if request.method == "POST":
        if request.POST.get('delete'):
            query = COWSNPhRRequest.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'cowsnphr/cowsnphr_home.html',
                  {
                      'projects': cowsnphr_projects
                  }
    )

@csrf_exempt  # needed or IE explodes
@login_required
def blob_cowsnphr(request):
    """
    View for COWSNPhR analyses on files already in blob storage 
    :param request: HttpRequest object
    """
    # Create forms for the COWSNPhRRequest model and the ContainerName autocomplete model
    cowsnphr_form = COWSNPhrForm()
    container_form = ContainerForm()
    # Set a variable to indicate that the sequence files are already in blob storage
    upload = False
    # Perform actions on a POST
    if request.method == 'POST':
        cowsnphr_form = COWSNPhrForm(request.POST, request.FILES)
        if cowsnphr_form.is_valid():
            # Populate a COWSNPhR entry with the details of the current analyses, but don't
            # commit the changes; the user needs to be added first
            cowsnphr = cowsnphr_form.save(commit=False)
            cowsnphr.user = request.user
            # Update the status to processing
            cowsnphr.status = 'Processing'
            cowsnphr.save()
            # Give the project a name based on the primary key if a project name was not provided
            if not cowsnphr.project_name:
                cowsnphr.project_name = str(cowsnphr)
            cowsnphr.save()

            # Create and submit the batch job
            run_cowsnphr_batch.apply_async(queue='cowbat', args=(cowsnphr.pk,), countdown=10)
            # Redirect the view to cowsnphr_processing
            return redirect('cowsnphr:cowsnphr_processing', cowsnphr_pk=cowsnphr.pk)

    return render(request,
                  'cowsnphr/cowsnphr_create.html',
                  {
                      'cowsnphr_form': cowsnphr_form,
                      'container_form': container_form,
                      'upload': upload
                  }
        )


@csrf_exempt  # needed or IE explodes
@login_required
def upload_cowsnphr(request):
    """
    View for COWSNPhR analyses requiring sequence files to be uploaded
    """
    # Create forms for the COWSNPhRRequest model
    cowsnphr_form = COWSNPhrForm()
    upload = True
    # Perform actions on a POST
    if request.method == 'POST':
        cowsnphr_form = COWSNPhrForm(request.POST, request.FILES, upload=True)
        if cowsnphr_form.is_valid():
            # Populate a COWSNPhRRequest entry with the details of the current analyses, but don't
            # commit the changes; the user needs to be added first
            cowsnphr = cowsnphr_form.save(commit=False)
            cowsnphr.user = request.user
            cowsnphr.save()
            # Give the project a name based on the primary key if a project name was not provided
            if not cowsnphr.project_name:
                cowsnphr.project_name = str(cowsnphr)
            # Also set the container name
            cowsnphr.container_name = str(cowsnphr)
            cowsnphr.save()
            # Redirect the view to the sequence upload page
            return redirect('cowsnphr:upload_cowsnphr_files', cowsnphr_pk=cowsnphr.pk)

    return render(request,
                  'cowsnphr/cowsnphr_create.html',
                  {
                      'cowsnphr_form': cowsnphr_form,
                      'upload': upload
                  }
        )


@csrf_exempt  # needed or IE explodes
@login_required
def seqid_cowsnphr(request):
    """
    Create a COWSNPhR request using SEQIDs
    """
    # Create forms for the COWSNPhRRequest model and the ContainerName autocomplete model
    cowsnphr_form = COWSNPhrSEQIDForm()
    # Perform actions on a POST
    if request.method == 'POST':
        cowsnphr_form = COWSNPhrSEQIDForm(request.POST)
        if cowsnphr_form.is_valid():
            cowsnphr = cowsnphr_form.save(commit=False)
            cowsnphr.user = request.user
            cowsnphr.seqids = cowsnphr_form.cleaned_data.get('seqids')
            cowsnphr.ref = cowsnphr_form.cleaned_data.get('ref')
            cowsnphr.save()
            # Give the project a name based on the primary key if a project name was not provided
            if not cowsnphr_form.cleaned_data.get('project_name'):
                cowsnphr.project_name = str(cowsnphr)
            # Set the name of the container
            cowsnphr.container_name = str(cowsnphr)
            cowsnphr.save()
            # Redirect the view to cowsnphr_processing
            return redirect('cowsnphr:cowsnphr_processing', cowsnphr_pk=cowsnphr.pk)
    return render(
        request,
        'cowsnphr/cowsnphr_seqid.html',
        {
            'cowsnphr_form': cowsnphr_form,
        }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def upload_cowsnphr_files(request, cowsnphr_pk):
    """
    View for uploading sequence files to blob storage
    """
    # Extract the COWSNPhR model using the supplied pk
    cowsnphr = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    if request.method == 'POST':
        for __, file in request.FILES.items():
            upload_files_for_cowsnphr(
                file=file,
                container_name=cowsnphr.container_name
            )

    return render(request,
                  'cowsnphr/cowsnphr_upload.html',
                  {
                      'cowsnphr': cowsnphr,
                  })


def container_refresh(request):
    """
    A view for a button to allow the user to refresh the containers in the populated autocomplete
    """
    # Refresh the database entries of the container names
    refresh_container_names()
    return redirect('cowsnphr:blob_cowsnphr')


# Has to be its own view; can't be mixed with others
class COWSNPhRAutoCompleter(autocomplete.Select2ListView):
    """
    Make a COWSNPhR-specific autocompleter
    """
    def __init__(self, **kwargs):
        self.category = 'run'
        super().__init__(**kwargs)

    def get_list(self):
        """
        Modify get_list to use COWSNPhR-specific components
        """
        # Create a query set of all the containers in the model
        query_set = ContainerName.objects.all()
        # Filter the query based on the text provided by the user in the autocomplete field
        if self.q:
            query_set.filter(container_name__icontains=self.q)
        # Return the sorted list of all the filtered containers
        return sorted(list(set(str(result.container_name) for result in query_set)))


@csrf_exempt  # needed or IE explodes
@login_required
def cowsnphr_processing(request, cowsnphr_pk):
    """
    View for the processing page
    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    # If the sequence files need to be uploaded, the batch job needs to be submitted
    if cowsnphr_request.status == 'Unprocessed':
        cowsnphr_request.status='Processing'
        cowsnphr_request.save()
        # Create and submit the batch job
        run_cowsnphr_batch.apply_async(queue='cowbat', args=(cowsnphr_pk,), countdown=10)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in cowsnphr_request.emails_array:
                cowsnphr_request.emails_array.append(email)
                cowsnphr_request.save()
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(request,
                  'cowsnphr/cowsnphr_processing.html',
                  {
                      'request': cowsnphr_request,
                      'form': form
                  })


def cowsnphr_reports(request, cowsnphr_pk):
    """
    Display the outputs from the COWSNPhR analyses
    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    return render(request,
                  'cowsnphr/cowsnphr_reports.html',
                  {
                      'request': cowsnphr_request,
                  }
            )

def cowsnphr_nucleotide_summary(request, cowsnphr_pk):
    """
    Display the nucleotide summary table
    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    return render(request,
                  'cowsnphr/cowsnphr_nucleotide_summary.html',
                  {
                      'request': cowsnphr_request,
                  }
            )

def cowsnphr_amino_acid_summary(request, cowsnphr_pk):
    """
    Display the amino acid summary table
    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    return render(request,
                  'cowsnphr/cowsnphr_amino_acid_summary.html',
                  {
                      'request': cowsnphr_request,
                  }
            )

def cowsnphr_tree(request, cowsnphr_pk):
    """
    Display the phylogenetic tree from the COWSNPhR analyses
    """
    cowsnphr_request = get_object_or_404(COWSNPhRRequest, pk=cowsnphr_pk)
    return render(request,
                  'cowsnphr/cowsnphr_tree.html',
                  {
                      'request': cowsnphr_request,
                  }
            )
