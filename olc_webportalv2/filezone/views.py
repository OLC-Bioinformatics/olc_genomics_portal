"""
View functions for FileZone app
"""

# Standard imports
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
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
# Third-party imports
from dal import autocomplete

# Azure imports
from azure.storage.blob import BlockBlobService

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
    return render(
        request,
        'filezone/filezone_home.html',
        {
            'projects': filezone_projects,
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
    return render(
        request,
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

            # Save the form data without committing to the database yet
            filezone = container_form.save(commit=False)

            # Set the container_name attribute of the filezone object
            # to the value obtained from the form's cleaned data
            filezone.container_name = container_form.cleaned_data.get(
                'container_name'
            )

            # Save the filezone object to the database
            filezone.save()

            # Add the container to the model
            ContainerName.objects.get_or_create(
                container_name=filezone.container_name
            )

            # Create the container
            create_container(container_name=filezone.container_name)

            # Redirect the view to the container view
            return redirect('filezone:container_view', filezone_pk=filezone.pk)
    return render(
        request,
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
    blob_data, blob_mapping_dict = list_blobs(filezone_pk=filezone_pk)

    if request.method == 'POST':
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )

        # Upload all the files. One at a time
        for __, file in request.FILES.items():
            messages.info(request, 'Uploading: ' + file.name)
            # Calculate the checksum
            blob_headers = calculate_checksum(item=file)

            # Upload to blob storage
            blob_client.create_blob_from_bytes(
                container_name=container.container_name,
                blob_name=file.name,
                blob=file.read(),
                content_settings=blob_headers
            )

        # Check if the form action specified in the POST request
        # is 'download'
        if request.POST.getlist("form-action") == ['download']:
            # Retrieve the list of selected options from the POST data
            selected_options = request.POST.getlist("selected_options[]")

            # Accessing the file path information
            download_blobs = [
                {
                    'blob_full_path': blob_mapping_dict[int(str_pk)]['blob_full_path']
                }
                for str_pk in selected_options if int(str_pk) in blob_mapping_dict
            ]

            # Generate a Shared Access Signature (SAS) URL for the
            # selected blobs to be downloaded
            sas_url = archive(
                blob_client=blob_client,
                blob_list=download_blobs,
                container_name=container.container_name,
                container_pk=container.pk
            )

        # Check if the form action specified in the POST request is
        # 'rename'
        if request.POST.getlist("form-action") == ['rename']:
            # Retrieve the original blob name from the POST data
            name = request.POST.getlist("blob_name")[0]
            # Retrieve the new name for the blob from the POST data
            rename = request.POST.getlist("blob_rename")[0]

            # Call the sanitise_rename function to check for illegal
            # characters in the new name
            illegal_chars = sanitise_rename(blob_rename=rename)

            # If no illegal characters are found, proceed with the renaming
            if not illegal_chars:
                # Attempt to rename the blob and store the result in 'info'
                info = rename_blob(
                    blob_name=name,
                    blob_rename=rename,
                    container_name=container.container_name
                )

                # If the rename operation was unsuccessful, display an
                # error message
                if info:
                    messages.error(
                        request,
                        _('Received the following message when attempting '
                            'to rename {blob_name} in container {container}: '
                            '{info}'
                            .format(
                                blob_name=name,
                                container=container.container_name,
                                info=info
                                )
                         )
                    )
            else:
                # If illegal characters are detected, display an error
                # message listing them
                messages.error(
                    request,
                    _('Detected the following illegal '
                        'character(s) {illegal} in {blob_rename}, the new '
                        'name supplied for {blob_name}'.format(
                            illegal=','.join(list(illegal_chars)),
                            blob_rename=rename,
                            blob_name=name
                        )
                     )
                )

        # Check if the form action specified in the POST request is 'delete'
        if request.POST.getlist("form-action") == ['delete']:
            # Retrieve the list of selected options (blob primary keys) from
            # the POST data
            selected_options = request.POST.getlist("selected_options[]")

            # Accessing the file path information for deletion
            delete_blobs = [
                {
                    'blob_full_path': blob_mapping_dict[int(str_pk)]['blob_full_path']
                }
                for str_pk in selected_options if int(str_pk) in blob_mapping_dict
            ]

            # Call the modified prep_delete_blob function
            errors, errored_blobs = prep_delete_blob(
                blob_client=blob_client,
                blob_list=delete_blobs,
                container_name=container.container_name
            )

            # If the deletion preparation returned an informational
            # message, display it as an error
            if errors:
                messages.error(
                    request,
                    _('Received the following message when attempting '
                        'to delete: {blobs} in container {container}'
                        .format(
                            blobs=errored_blobs,
                            container=container.container_name
                        )
                        )
                )

        # Repopulate the blob information
        blob_data, __ = list_blobs(filezone_pk=filezone_pk)

        return render(
            request,
            'filezone/container_view.html',
            {
                'blobs': blob_data,
                'container': container,
                'sas_url': sas_url,
            }
        )
    return render(
        request,
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
    # Initialize the form for file location
    file_locate_form = FileLocateForm()
    if request.method == 'POST':
        # If the request is POST, populate the form with POST data
        file_locate_form = FileLocateForm(request.POST)

        if file_locate_form.is_valid():
            # If form data is valid, proceed to create a Regexes object
            filezone = file_locate_form.save(commit=False)

            # Extract and set container regex from the form's cleaned data
            filezone.container_regex = file_locate_form.cleaned_data.get(
                'container_regex'
            )

            # Extract and set container exclude regex
            filezone.container_exclude_regex = \
                file_locate_form.cleaned_data.get('container_exclude_regex')

            # Extract and set file regex
            filezone.file_regex = file_locate_form.cleaned_data.get(
                'file_regex'
            )

            # Extract and set file exclude regex
            filezone.file_exclude_regex = \
                file_locate_form.cleaned_data.get('file_exclude_regex')

            # Extract and set container regex list
            filezone.container_regex_list = \
                file_locate_form.cleaned_data.get('container_regex_list')

            # Extract and set container exclude regex list
            filezone.container_exclude_regex_list = \
                file_locate_form.cleaned_data.get(
                    'container_exclude_regex_list'
                )

            # Extract and set file regex list
            filezone.file_regex_list = \
                file_locate_form.cleaned_data.get('file_regex_list')

            # Extract and set file exclude regex list
            filezone.file_exclude_regex_list = \
                file_locate_form.cleaned_data.get('file_exclude_regex_list')

            # Save the filezone object to the database
            filezone.save()

            # Redirect to the filezone_processing view
            return redirect('filezone:filezone_processing',
                            filezone_pk=filezone.pk)
    return render(
        request,
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
    # If the sequence files need to be uploaded, the batch job needs to be
    # submitted
    if filezone_request.status == 'Unprocessed':
        filezone_request.status = 'Processing'
        filezone_request.save()
        # Create and submit the batch job
        locate_files.apply_async(
            queue='cowbat', args=(filezone_pk,), countdown=10
        )
    return render(
        request,
        'filezone/filezone_processing.html',
        {
            'filezone_request': filezone_request,
        }
    )


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
        settings.STATIC_URL, 'ajax', 'filezone', str(filezone_pk), 'arrays.txt'
    )
    if request.method == 'POST':
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )
        # Initialise a list to store the selected options
        selected_options = []
        selected_options_list = request.POST.getlist("selected_options")

        # The selected_options are returned as a list with a single
        # comma-separated string of all the selections when using javascript
        for item in selected_options_list:
            # Create a new list by splitting the comma-separated string
            selected_options = item.split(',')
        if request.POST.getlist("form-action") == ['download']:
            # Initialise lists to store blob dictionaries and container names
            # of the selected blobs
            download_blobs = []
            container_list = []
            for container_name, blob_list in \
                    filezone_request.file_matches.items():
                for blob_dict in blob_list:
                    for str_pk in selected_options:
                        if int(str_pk) == blob_dict['pk']:

                            # Add the dictionary and container name to the
                            # appropriate lists
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
            destination_container = request.POST.getlist(
                "desired_container"
            )[0]

            # Determine which blobs are to be copied
            for container_name, blob_list in \
                    filezone_request.file_matches.items():
                for blob_dict in blob_list:

                    # Iterate over all the user-selected blobs to copy
                    for str_pk in selected_options:

                        # Find if the primary key of the blob in the
                        # dictionary matches the primary key of the desired
                        # blob to download
                        if int(str_pk) == blob_dict['pk']:

                            # Add blob dictionary to the list
                            blob_list.append(blob_dict)
            # Copy the blobs to the desired container
            copy_blobs(
                blob_metadata_list=blob_list,
                destination_container=destination_container
            )
        return render(
            request,
            'filezone/file_view.html',
            {
                'filezone_request': filezone_request,
                'json_results': data_tables_path,
                'sas_url': sas_url
            }
        )
    return render(
        request,
        'filezone/file_view.html',
        {
            'filezone_request': filezone_request,
            'json_results': data_tables_path
        }
    )


def container_refresh(__):
    """
    A view for a button to allow the user to refresh the containers in the
    populated autocomplete
    """
    # Refresh the database entries of the container names
    refresh_container_names()
    return redirect('filezone:container_select')


# Has to be its own view; can't be mixed with others
class FileZoneAutoCompleter(autocomplete.Select2ListView):
    """
    Make a FileZone-specific auto-completer
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
        # Filter the query based on the text provided by the user in the
        # autocomplete field
        if self.q:
            query_set.filter(container_name__icontains=self.q)
        # Return the sorted list of all the filtered containers
        return sorted(
            list(set(str(result.container_name) for result in query_set))
        )
