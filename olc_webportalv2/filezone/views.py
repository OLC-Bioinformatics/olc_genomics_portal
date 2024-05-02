import base64
import hashlib
import os

# Django imports
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import (
    get_object_or_404, \
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

# Azure imports
from azure.storage.blob import BlockBlobService
from azure.storage.blob import ContentSettings

# Portal-specific imports
from olc_webportalv2.filezone.forms import (
    ContainerCreateForm,
    ContainerForm,
    ContainerSelectForm,
    FileLocateForm
)
from olc_webportalv2.filezone.methods import (
    calculate_checksum,
    copy_blobs,
)
from olc_webportalv2.filezone.models import (
    ContainerName,
    Regexes
)
from olc_webportalv2.filezone.tasks import (
    archive,
    archive_multiple_containers,
    create_container,
    list_blobs,
    locate_files,
    prep_delete_blob,
    refresh_container_names,
    rename_blob,
    sanitise_rename
)


@csrf_exempt  # needed or IE explodes
@login_required
def filezone_home(request):
    """
    View for FileZone homepage
    :param request: HttpRequest object
    """
    filezone_projects = Regexes.objects.all()
    return render(request,
                  'filezone/filezone_home.html',
                  {
                      'projects': filezone_projects
                  }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def container_select(request):
    """
    View for FileZone container select
    :param request: HttpRequest object
    """
    # Create a form for the ContainerName autocomplete model
    container_form = ContainerForm()
    container_select_form = ContainerSelectForm()
    # Perform actions on a POST
    if request.method == 'POST':
        container_select_form = ContainerSelectForm(request.POST)
        if container_select_form.is_valid():
            filezone = container_select_form.save()
            # Redirect the view to the container view
            return redirect('filezone:container_view', filezone_pk=filezone.pk)
    return render(request,
                  'filezone/container_select.html',
                  {
                      'container_form': container_form,
                      'container_select_form': container_select_form
                  }
        )

@csrf_exempt  # needed or IE explodes
@login_required
def container_create(request):
    """
    View to create new containers
    """
    # Create a form for the ContainerName autocomplete model
    container_form = ContainerCreateForm()
    # Perform actions on a POST
    if request.method == 'POST':
        container_form = ContainerCreateForm(request.POST)
        if container_form.is_valid():
            
            filezone = container_form.save(commit=False)
            filezone.container_name = container_form.cleaned_data.get('container_name')
            filezone.save()
            # Add the container to the model
            ContainerName.objects.get_or_create(container_name=filezone.container_name)
            # Create the container
            create_container(container_name=filezone.container_name)
            # Redirect the view to the container view
            return redirect('filezone:container_view', filezone_pk=filezone.pk)
    return render(request,
                  'filezone/container_create.html',
                  {
                      'container_form': container_form,
                  }
        )


@csrf_exempt  # needed or IE explodes
@login_required
def container_view(request, filezone_pk):
    """
    View to manage container contents
    """
    sas_url = ''
    # Extract the FileZone model using the supplied pk
    container = get_object_or_404(ContainerName, pk=filezone_pk)
    # Find all the blobs in the container.
    blob_data = list_blobs(filezone_pk=filezone_pk)
    if request.method == 'POST':
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )
        # Upload all the files. One at a time
        for i in range(0, len(request.FILES)):
            item = request.FILES.get('file[%d]' % i)
            # Calculate the checksum
            blob_headers = calculate_checksum(item=item)
            # Upload to blob storage
            blob_client.create_blob_from_bytes(
                container_name=container.container_name,
                blob_name=item.name,
                blob=item.read(),
                content_settings=blob_headers
            )

        if request.POST.getlist("form-action") == ['download']:
            selected_options = request.POST.getlist("selected_options")
            download_blobs = []
            for blob_dict in blob_data:
                for str_pk in selected_options:
                    if int(str_pk) == blob_dict['pk']:
                        download_blobs.append(blob_dict)
            sas_url = archive(
                blob_client=blob_client,
                blob_list=download_blobs,
                container_name=container.container_name,
                container_pk=container.pk
            )
            
        if request.POST.getlist("form-action") == ['rename']:
            name = request.POST.getlist("blob_name")[0]
            rename = request.POST.getlist("blob_rename")[0]
            illegal_chars = sanitise_rename(blob_rename=rename)
            if not illegal_chars:
                info = rename_blob(
                    blob_name=name,
                    blob_rename=rename,
                    container_name=container.container_name
                )
                if info:
                    messages.error(
                        request,
                        'Received the following message when attempting to rename {blob_name} in '
                        'container {container}'.format(
                            blob_name=name,
                            container=container.container_name
                        ))
            else:
                messages.error(request, 'Detected the following illegal character(s) {illegal} in '
                    '{blob_rename}, the new name supplied for {blob_name}'.format(
                        illegal=','.join(list(illegal_chars)),
                        blob_rename=rename,
                        blob_name=name
                    )
                )

        if request.POST.getlist("form-action") == ['delete']:
            selected_options = request.POST.getlist("selected_options")
            delete_blobs = []
            for blob_dict in blob_data:
                for str_pk in selected_options:
                    if int(str_pk) == blob_dict['pk']:
                        delete_blobs.append(blob_dict)
            # messages.info(request, blobs)
            info, _ = prep_delete_blob(
                blob_client=blob_client,
                blob_list=delete_blobs,
                container_name=container.container_name
            )
            if info:
                messages.error(
                    request,
                    'Received the following message when attempting to delete {blob_name} in '
                    'container {container}'.format(
                        blob_name=name,
                        container=container.container_name
                    )
                )

        # Repopulate the blob information
        blob_data = list_blobs(filezone_pk=filezone_pk)

        return render(request,
                  'filezone/container_view.html',
                  {
                      'blobs': blob_data,
                      'container': container,
                      'sas_url': sas_url,
                  }
        )
    return render(request,
                  'filezone/container_view.html',
                  {
                      'blobs': blob_data,
                      'container': container,
                      'sas_url': sas_url,
                  }
        )

@csrf_exempt  # needed or IE explodes
@login_required
def file_select(request):
    """
    View to allow user locate files in blob storage with file regex patterns
    """
    # Create the form
    file_locate_form = FileLocateForm()
    if request.method == 'POST':
        file_locate_form = FileLocateForm(request.POST)
        if file_locate_form.is_valid():
            # Create a Regexes object from the form
            filezone = file_locate_form.save(commit=False)
            filezone.container_regex = file_locate_form.cleaned_data.get('container_regex')
            filezone.container_exclude_regex = \
                file_locate_form.cleaned_data.get('container_exclude_regex')
            filezone.file_regex = file_locate_form.cleaned_data.get('file_regex')
            filezone.file_exclude_regex = file_locate_form.cleaned_data.get('file_exclude_regex')
            filezone.container_regex_list = \
                file_locate_form.cleaned_data.get('container_regex_list')
            filezone.container_exclude_regex_list = \
                file_locate_form.cleaned_data.get('container_exclude_regex_list')
            filezone.file_regex_list = file_locate_form.cleaned_data.get('file_regex_list')
            filezone.file_exclude_regex_list = \
                file_locate_form.cleaned_data.get('file_exclude_regex_list')
            filezone.save()
            # Redirect the view to filezone_processing
            return redirect('filezone:filezone_processing', filezone_pk=filezone.pk)
    return render(request,
                  'filezone/file_select.html',
                  {
                      'file_select_form': file_locate_form
                  }
        )


@csrf_exempt  # needed or IE explodes
@login_required
def filezone_processing(request, filezone_pk):
    """
    View for the processing page
    """
    filezone_request = get_object_or_404(Regexes, pk=filezone_pk)
    # If the sequence files need to be uploaded, the batch job needs to be submitted
    if filezone_request.status == 'Unprocessed':
        filezone_request.status='Processing'
        filezone_request.save()
        # Create and submit the batch job
        locate_files.apply_async(queue='cowbat', args=(filezone_pk,), countdown=10)
    return render(request,
                  'filezone/filezone_processing.html',
                  {
                      'filezone_request': filezone_request,
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def file_view(request, filezone_pk):
    """
    View for the located files
    """
    sas_url = ''
    filezone_request = get_object_or_404(Regexes, pk=filezone_pk)
    # Send the location of the ajax file to the view
    data_tables_path = os.path.join(
        settings.STATIC_URL, 'ajax', 'filezone',str(filezone_pk), 'arrays.txt'
    )
    if request.method == 'POST':
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )
        # Initialise a list to store the selected options
        selected_options = []
        selected_options_list = request.POST.getlist("selected_options")
        # The selected_options are returned as a list with a single comma-separated string
        # of all the selections when using javascript
        for item in selected_options_list:
            # Create a new list by splitting the comma-separated string
            selected_options = item.split(',')
        if request.POST.getlist("form-action") == ['download']:
            # Initialise lists to store blob dictionaries and container names of the selected blobs
            download_blobs = []
            container_list = []
            for container_name, blob_list in filezone_request.file_matches.items():
                for blob_dict in blob_list:
                    for str_pk in selected_options:
                        if int(str_pk) == blob_dict['pk']:
                            # Add the dictionary and container name to the appropriate lists
                            download_blobs.append(blob_dict)
                            container_list.append(container_name)
            sas_url = archive_multiple_containers(
                blob_client=blob_client,
                blob_list=download_blobs,
                container_list=container_list,
                filezone_pk=filezone_request.pk
            )
        if request.POST.getlist("form-action") == ['copy']:
            # Initialise a list to store blob dictionaries
            blob_list = []
            # Extract the name of the destination container from the request
            destination_container = request.POST.getlist("desired_container")[0]
            # Determine which blobs are to be copied
            for container_name, blob_list in filezone_request.file_matches.items():
                for blob_dict in blob_list:
                    # Iterate over all the user-selected blobs to copy
                    for str_pk in selected_options:
                        # Find if the primary key of the blob in the dictionary matches the primary
                        # key of the desired blob to download
                        if int(str_pk) == blob_dict['pk']:
                            # Add blob dictionary to the list
                            blob_list.append(blob_dict)
            # Copy the blobs to the desired container
            copy_blobs(
                blob_metadata_list=blob_list,
                destination_container=destination_container
            )
        return render(request,
                  'filezone/file_view.html',
                  {
                      'filezone_request': filezone_request,
                      'json_results': data_tables_path,
                      'sas_url': sas_url
                  })
    return render(request,
                  'filezone/file_view.html',
                  {
                      'filezone_request': filezone_request,
                      'json_results': data_tables_path
                  })


def container_refresh(request):
    """
    A view for a button to allow the user to refresh the containers in the populated autocomplete
    """
    # Refresh the database entries of the container names
    refresh_container_names()
    return redirect('filezone:container_select')

# Has to be its own view; can't be mixed with others
class FileZoneAutoCompleter(autocomplete.Select2ListView):
    """
    Make a FileZone-specific autocompleter
    """
    def __init__(self, **kwargs):
        self.category = 'run'
        super().__init__(**kwargs)

    def get_list(self):
        """
        Modify get_list to use FileZone-specific components
        """
        # Create a query set of all the containers in the model
        query_set = ContainerName.objects.all()
        # Filter the query based on the text provided by the user in the autocomplete field
        if self.q:
            query_set.filter(container_name__icontains=self.q)
        # Return the sorted list of all the filtered containers
        return sorted(list(set(str(result.container_name) for result in query_set)))
