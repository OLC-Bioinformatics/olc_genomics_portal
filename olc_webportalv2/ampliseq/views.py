"""
Views for AmpliSeq analyses
"""

# Standard imports
import os


# Django
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import (
    get_object_or_404, \
    redirect, \
    render
)
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

# Azure
from azure.storage.blob import BlockBlobService

# Third-party imports
from dal import autocomplete

# Portal-specific imports
from olc_webportalv2.ampliseq.forms import (
    AmpliseqForm,
    ContainerForm
)
from olc_webportalv2.ampliseq.models import (
    AmpliSeqRequest,
    ContainerName
)
from olc_webportalv2.ampliseq.tasks import (
    download_reports,
    refresh_container_names,
    run_ampliseq_batch,
    upload_file
)
from olc_webportalv2.geneseekr.forms import EmailForm


# Create your views here.
@csrf_exempt  # needed or IE explodes
@login_required
def ampliseq_home(request):
    """
    View for AmpliSeq homepage
    :param request: HttpRequest object
    """
    ampliseq_projects = AmpliSeqRequest.objects.filter(user=request.user)
    if request.method == "POST":
        if request.POST.get('delete'):
            query = AmpliSeqRequest.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'ampliseq/ampliseq_home.html',
                  {
                      'projects': ampliseq_projects
                  }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def blob_ampliseq(request):
    """
    View for AmpliSeq analyses on files already in blob storage 
    :param request: HttpRequest object
    """
    # Create forms for the AmpliSeqRequest model and the ContainerName autocomplete model
    ampliseq_form = AmpliseqForm()
    container_form = ContainerForm()
    # Set a variable to indicate that the sequence files are already in blob storage
    upload = False
    # Perform actions on a POST
    if request.method == 'POST':
        ampliseq_form = AmpliseqForm(request.POST, request.FILES)
        if ampliseq_form.is_valid():
            # Populate a AmpliSeqRequest entry with the details of the current analyses, but don't
            # commit the changes; the user needs to be added first
            ampliseq = ampliseq_form.save(commit=False)
            ampliseq.user = request.user
            # Update the status to processing
            ampliseq.status = 'Processing'
            ampliseq.save()
            # Give the project a name based on the primary key if a project name was not provided
            if not ampliseq.project_name:
                ampliseq.project_name = str(ampliseq)
            ampliseq.save()
            # Upload the metadata file and classifier
            if ampliseq.metadata:
                ampliseq.metadata.file_name = request.FILES["metadata"]
                upload_file(
                    file=ampliseq.metadata.file_name,
                    container_name=ampliseq.container_name
                )
                # Remove the temporary file from disk
                os.remove(ampliseq.metadata.path)

            # Upload the classifier file (if it was provided)
            if ampliseq.classifier:
                ampliseq.classifier.file_name = request.FILES["classifier"]
                upload_file(
                    file=ampliseq.classifier.file_name,
                    container_name=ampliseq.container_name
                )
                os.remove(ampliseq.classifier.path)
            # Create and submit the batch job
            run_ampliseq_batch.apply_async(queue='cowbat', args=(ampliseq.pk,), countdown=10)
            # Redirect the view to ampliseq_processing
            return redirect('ampliseq:ampliseq_processing', ampliseq_pk=ampliseq.pk)

    return render(request,
                  'ampliseq/ampliseq_create.html',
                  {
                      'ampliseq_form': ampliseq_form,
                      'container_form': container_form,
                      'upload': upload
                  }
        )

def upload_ampliseq(request):
    """
    View for AmpliSeq analyses requiring sequence files to be uploaded
    """
    # Create forms for the AmpliSeqRequest model and the ContainerName autocomplete model
    ampliseq_form = AmpliseqForm()
    container_form = ContainerForm()
    # Set a variable to indicate that the sequence files are to be uploaded
    upload = True
    # Perform actions on a POST
    if request.method == 'POST':
        ampliseq_form = AmpliseqForm(request.POST, request.FILES)
        ampliseq_form.upload = True
        if ampliseq_form.is_valid():
            # Populate a AmpliSeqRequest entry with the details of the current analyses, but don't
            # commit the changes; the user needs to be added first
            ampliseq = ampliseq_form.save(commit=False)
            ampliseq.user = request.user
            ampliseq.save()
            # Give the project a name based on the primary key if a project name was not provided
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
                    container_name=ampliseq.container_name
                )
                # Remove the temporary file from disk
                os.remove(ampliseq.metadata.path)

            # Upload the classifier file (if it was provided)
            if ampliseq.classifier:
                ampliseq.classifier.file_name = request.FILES["classifier"]
                upload_file(
                    file=ampliseq.classifier.file_name,
                    container_name=ampliseq.container_name
                )
                os.remove(ampliseq.classifier.path)
            # Redirect the view to the sequence upload page
            return redirect('ampliseq:upload_ampliseq_files', ampliseq_pk=ampliseq.pk)

    return render(request,
                  'ampliseq/ampliseq_create.html',
                  {
                      'ampliseq_form': ampliseq_form,
                      'container_form': container_form,
                      'upload': upload
                  }
        )

def upload_ampliseq_files(request, ampliseq_pk):
    """
    View for uploading sequence files to blob storage
    """
    # Extract the AmpliSeq model using the supplied pk
    ampliseq = get_object_or_404(AmpliSeqRequest, pk=ampliseq_pk)
    if request.method == 'POST':
        for _, file in request.FILES.items():
            upload_file(
                file=file,
                container_name=ampliseq.container_name
            )

    return render(request,
                  'ampliseq/upload_sequence_data.html',
                  {
                      'ampliseq': ampliseq,
                  })


def container_refresh(request):
    """
    A view for a button to allow the user to refresh the containers in the populated autocomplete
    """
    # Refresh the database entries of the container names
    refresh_container_names()
    return redirect('ampliseq:blob_ampliseq')


# Has to be its own view; can't be mixed with others
class AmpliSeqAutoCompleter(autocomplete.Select2ListView):
    """
    Make a AmpliSeq-specific autocompleter
    """
    def __init__(self, **kwargs):
        self.category = 'run'
        super().__init__(**kwargs)

    def get_list(self):
        """
        Modify get_list to use AmpliSeq-specific components
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
def ampliseq_processing(request, ampliseq_pk):
    """
    View for the processing page
    """
    ampliseq_request = get_object_or_404(AmpliSeqRequest, pk=ampliseq_pk)
    # If the sequence files need to be uploaded, the batch job needs to be submitted
    if ampliseq_request.status == 'Unprocessed':
        ampliseq_request.status='Processing'
        ampliseq_request.save()
        # Create and submit the batch job
        run_ampliseq_batch.apply_async(queue='cowbat', args=(ampliseq_pk,), countdown=10)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in ampliseq_request.emails_array:
                ampliseq_request.emails_array.append(email)
                ampliseq_request.save()
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(request,
                  'ampliseq/ampliseq_processing.html',
                  {
                      'request': ampliseq_request,
                      'form': form
                  })

@csrf_exempt  # needed or IE explodes
@login_required
def ampliseq_report(request, ampliseq_pk):
    """
    View for AmpliSeq report page
    :param request: HttpRequest object
    """
    ampliseq_request = get_object_or_404(AmpliSeqRequest, pk=ampliseq_pk)
    report = 'ampliseq/{container_name}_execution_report.html'.format(
        container_name=ampliseq_request.container_name
    )
    if not os.path.isfile(os.path.join('olc_webportalv2', 'templates', report)):
        download_reports(ampliseq=ampliseq_request)
    return render(
        request,
        report,
        {}
    )


@csrf_exempt  # needed or IE explodes
@login_required
def ampliseq_timeline(request, ampliseq_pk):
    """
    View for AmpliSeq timeline page
    :param request: HttpRequest object
    """
    ampliseq_request = get_object_or_404(AmpliSeqRequest, pk=ampliseq_pk)
    timeline = 'ampliseq/{container_name}_execution_timeline.html'.format(
        container_name=ampliseq_request.container_name
        )
    if not os.path.isfile(os.path.join('olc_webportalv2', 'templates', timeline)):
        download_reports(ampliseq=ampliseq_request)
    return render(
        request,
        timeline,
        {}
    )
