"""Views for the FileZone application."""

# Standard imports
import logging
import os

# Third-party imports
from dal import autocomplete

# Django imports
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from olc_webportalv2.common.storage_client import (
    StorageServiceError,
    get_storage_client,
)

# Portal-specific imports
from olc_webportalv2.filezone.forms import (
    ContainerCreateForm,
    ContainerForm,
    FileLocateForm,
)
from olc_webportalv2.filezone.methods import (
    calculate_checksum,
    copy_blobs,
)
from olc_webportalv2.filezone.models import ContainerName, Regexes
from olc_webportalv2.filezone.tasks import (
    archive,
    archive_multiple_containers,
    create_container,
    list_blobs,
    locate_files,
    prep_delete_blob,
    refresh_container_names,
    rename_blob,
    sanitise_rename,
)

logger = logging.getLogger(__name__)


@csrf_exempt  # needed or IE explodes
@login_required
def filezone_home(request):
    """Render the FileZone homepage.

    Args:
        request: Current HTTP request.

    """
    filezone_projects = Regexes.objects.all()
    return render(
        request,
        'filezone/filezone_home.html',
        {
            'projects': filezone_projects,
        },
    )


@csrf_exempt  # needed or IE explodes
@login_required
def container_select(request):
    """Select a registered storage container.

    Args:
        request: Current HTTP request.

    """
    # Create a form for the ContainerName autocomplete model
    container_form = ContainerForm()
    # Perform actions on a POST
    if request.method == 'POST':
        container_form = ContainerForm(request.POST)
        if container_form.is_valid():
            filezone = container_form.cleaned_data['container_name']
            # Redirect the view to the container view
            return redirect('filezone:container_view', filezone_pk=filezone.pk)
    return render(
        request,
        'filezone/container_select.html',
        {
            'container_form': container_form,
        },
    )


@csrf_exempt  # needed or IE explodes
@login_required
def container_create(request):
    """Create a storage container and register it in the portal.

    Args:
        request: Current HTTP request.

    """
    # Create a form for the ContainerName autocomplete model
    container_form = ContainerCreateForm()
    # Perform actions on a POST
    if request.method == 'POST':
        container_form = ContainerCreateForm(request.POST)
        if container_form.is_valid():
            filezone = container_form.save(commit=False)

            try:
                create_container(container_name=filezone.container_name)
            except StorageServiceError:
                logger.exception(
                    'Could not create FileZone container %s',
                    filezone.container_name,
                )
                messages.error(
                    request,
                    _('Unable to create the requested storage container.'),
                )
                return render(
                    request,
                    'filezone/container_create.html',
                    {'container_form': container_form},
                )

            filezone.save()

            # Redirect the view to the container view
            return redirect('filezone:container_view', filezone_pk=filezone.pk)
    return render(
        request,
        'filezone/container_create.html',
        {
            'container_form': container_form,
        },
    )


@csrf_exempt  # needed or IE explodes
@login_required
def container_view(request, filezone_pk):
    """Display and manage the contents of a storage container.

    Args:
        request: Current HTTP request.
        filezone_pk: Primary key of the selected container.

    """
    sas_url = ''
    # Extract the FileZone model using the supplied pk
    container = get_object_or_404(ContainerName, pk=filezone_pk)

    # Find all the blobs in the container.
    blob_data, blob_mapping_dict = list_blobs(filezone_pk=filezone_pk)

    if request.method == 'POST':
        storage = get_storage_client()

        # Upload all the files. One at a time
        for _, file in request.FILES.items():
            messages.info(request, f'Uploading: {file.name}')
            # Calculate the checksum
            blob_md5 = calculate_checksum(item=file)

            # Upload to blob storage
            storage.upload_bytes(
                container.container_name,
                file.name,
                file.read(),
                content_type=getattr(file, 'content_type', None),
                content_md5=blob_md5,
            )

        # Check if the form action specified in the POST request
        # is 'download'
        if request.POST.getlist('form-action') == ['download']:
            # Retrieve the list of selected options from the POST data
            selected_options = request.POST.getlist('selected_options[]')

            # Accessing the file path information
            download_blobs = [
                {
                    'blob_full_path': blob_mapping_dict[int(str_pk)][
                        'blob_full_path'
                    ]
                }
                for str_pk in selected_options
                if int(str_pk) in blob_mapping_dict
            ]

            # Generate a Shared Access Signature (SAS) URL for the
            # selected blobs to be downloaded
            sas_url = archive(
                blob_client=storage,
                blob_list=download_blobs,
                container_name=container.container_name,
                container_pk=container.pk,
            )

        # Check if the form action specified in the POST request is
        # 'rename'
        if request.POST.getlist('form-action') == ['rename']:
            # Retrieve the original blob name from the POST data
            name = request.POST.getlist('blob_name')[0]
            # Retrieve the new name for the blob from the POST data
            rename = request.POST.getlist('blob_rename')[0]

            # Call the sanitise_rename function to check for illegal
            # characters in the new name
            illegal_chars = sanitise_rename(blob_rename=rename)

            # If no illegal characters are found, proceed with the renaming
            if not illegal_chars:
                # Attempt to rename the blob and store the result in 'info'
                info = rename_blob(
                    blob_name=name,
                    blob_rename=rename,
                    container_name=container.container_name,
                )

                # If the rename operation was unsuccessful, display an
                # error message
                if info:
                    messages.error(
                        request,
                        _(
                            'Received the following message when attempting '
                            'to rename %(blob_name)s in container '
                            '%(container)s: %(info)s'
                        )
                        % {
                            'blob_name': name,
                            'container': container.container_name,
                            'info': info,
                        },
                    )
            else:
                # If illegal characters are detected, display an error
                # message listing them
                messages.error(
                    request,
                    _(
                        'Detected the following illegal '
                        'character(s) %(illegal)s in %(blob_rename)s, the '
                        'new name supplied for %(blob_name)s'
                    )
                    % {
                        'illegal': ','.join(illegal_chars),
                        'blob_rename': rename,
                        'blob_name': name,
                    },
                )

        # Check if the form action specified in the POST request is 'delete'
        if request.POST.getlist('form-action') == ['delete']:
            # Retrieve the list of selected options (blob primary keys) from
            # the POST data
            selected_options = request.POST.getlist('selected_options[]')

            # Accessing the file path information for deletion
            delete_blobs = [
                {
                    'blob_full_path': blob_mapping_dict[int(str_pk)][
                        'blob_full_path'
                    ]
                }
                for str_pk in selected_options
                if int(str_pk) in blob_mapping_dict
            ]

            # Call the modified prep_delete_blob function
            errors, errored_blobs = prep_delete_blob(
                blob_client=storage,
                blob_list=delete_blobs,
                container_name=container.container_name,
            )

            # If the deletion preparation returned an informational
            # message, display it as an error
            if errors:
                messages.error(
                    request,
                    _(
                        'Received the following message when attempting '
                        'to delete: %(blobs)s in container %(container)s'
                    )
                    % {
                        'blobs': errored_blobs,
                        'container': container.container_name,
                    },
                )

        # Repopulate the blob information
        blob_data, _ = list_blobs(filezone_pk=filezone_pk)

        return render(
            request,
            'filezone/container_view.html',
            {
                'blobs': blob_data,
                'container': container,
                'sas_url': sas_url,
            },
        )
    return render(
        request,
        'filezone/container_view.html',
        {
            'blobs': blob_data,
            'container': container,
            'sas_url': sas_url,
        },
    )


@csrf_exempt  # needed or IE explodes
@login_required
def file_select(request):
    """Create a search request for blobs matching user patterns.

    Args:
        request: Current HTTP request.

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
            filezone.container_exclude_regex = (
                file_locate_form.cleaned_data.get('container_exclude_regex')
            )

            # Extract and set file regex
            filezone.file_regex = file_locate_form.cleaned_data.get(
                'file_regex'
            )

            # Extract and set file exclude regex
            filezone.file_exclude_regex = file_locate_form.cleaned_data.get(
                'file_exclude_regex'
            )

            # Extract and set container regex list
            filezone.container_regex_list = file_locate_form.cleaned_data.get(
                'container_regex_list'
            )

            # Extract and set container exclude regex list
            filezone.container_exclude_regex_list = (
                file_locate_form.cleaned_data.get(
                    'container_exclude_regex_list'
                )
            )

            # Extract and set file regex list
            filezone.file_regex_list = file_locate_form.cleaned_data.get(
                'file_regex_list'
            )

            # Extract and set file exclude regex list
            filezone.file_exclude_regex_list = (
                file_locate_form.cleaned_data.get('file_exclude_regex_list')
            )

            # Save the filezone object to the database
            filezone.save()

            # Redirect to the filezone_processing view
            return redirect(
                'filezone:filezone_processing', filezone_pk=filezone.pk
            )
    return render(
        request,
        'filezone/file_select.html',
        {'file_select_form': file_locate_form},
    )


@csrf_exempt  # needed or IE explodes
@login_required
def filezone_processing(request, filezone_pk):
    """Queue an unprocessed search and render its status page.

    Args:
        request: Current HTTP request.
        filezone_pk: Primary key of the search request.

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
        },
    )


@csrf_exempt  # needed or IE explodes
@login_required
def file_view(request, filezone_pk):
    """Display search results and process selected blobs.

    Args:
        request: Current HTTP request.
        filezone_pk: Primary key of the search request.

    """
    sas_url = ''
    filezone_request = get_object_or_404(Regexes, pk=filezone_pk)
    # Send the location of the ajax file to the view
    data_tables_path = os.path.join(
        settings.STATIC_URL, 'ajax', 'filezone', str(filezone_pk), 'arrays.txt'
    )
    if request.method == 'POST':
        storage = get_storage_client()
        file_matches = filezone_request.file_matches or {}
        # Initialise a list to store the selected options
        selected_options = []
        selected_options_list = request.POST.getlist('selected_options')

        # The selected_options are returned as a list with a single
        # comma-separated string of all the selections when using javascript
        for item in selected_options_list:
            # Create a new list by splitting the comma-separated string
            selected_options = item.split(',')
        if request.POST.getlist('form-action') == ['download']:
            # Collect selected blobs from the file-location results.
            download_blobs = []
            for blob_list in file_matches.values():
                for blob_dict in blob_list:
                    for str_pk in selected_options:
                        if int(str_pk) == blob_dict['pk']:
                            # Add the dictionary and container name to the
                            # appropriate lists
                            download_blobs.append(blob_dict)
            sas_url = archive_multiple_containers(
                blob_client=storage,
                blob_list=download_blobs,
                filezone_pk=filezone_request.pk,
            )
        if request.POST.getlist('form-action') == ['copy']:
            # Initialise a list to store the selected blob dictionaries
            selected_blobs = []

            # Extract the name of the destination container from the request
            destination_container = request.POST.getlist('desired_container')[
                0
            ]

            # Determine which blobs are to be copied
            for container_blobs in file_matches.values():
                for blob_dict in container_blobs:
                    # Iterate over all the user-selected blobs to copy
                    for str_pk in selected_options:
                        # Find if the primary key of the blob in the
                        # dictionary matches the primary key of the desired
                        # blob to download
                        if int(str_pk) == blob_dict['pk']:
                            selected_blobs.append(blob_dict)
            # Copy the blobs to the desired container
            copy_blobs(
                blob_metadata_list=selected_blobs,
                destination_container=destination_container,
            )
        return render(
            request,
            'filezone/file_view.html',
            {
                'filezone_request': filezone_request,
                'json_results': data_tables_path,
                'sas_url': sas_url,
            },
        )
    return render(
        request,
        'filezone/file_view.html',
        {
            'filezone_request': filezone_request,
            'json_results': data_tables_path,
        },
    )


def container_refresh(_request):
    """Refresh container autocomplete data and return to container selection.

    Args:
        _request: Current HTTP request.

    """
    # Refresh the database entries of the container names
    refresh_container_names()
    return redirect('filezone:container_select')


# Has to be its own view; can't be mixed with others
class FileZoneAutoCompleter(autocomplete.Select2QuerySetView):
    """Provide FileZone container autocomplete results."""

    def get_queryset(self):
        """Return container choices ordered by name."""
        query_set = ContainerName.objects.all()
        if self.q:
            query_set = query_set.filter(container_name__icontains=self.q)
        return query_set.order_by('container_name', 'pk').distinct(
            'container_name'
        )
