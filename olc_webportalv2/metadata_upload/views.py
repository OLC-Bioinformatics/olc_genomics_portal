"""Views for the Metadata Upload workflow."""

import logging

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.shortcuts import redirect, render

from olc_webportalv2.metadata_upload.forms import UploadFileForm

from .management.commands.upload_metadata import upload_metadata

logger = logging.getLogger(__name__)


def upload_files(request):
    """Validate uploaded CSVs and run the metadata importer.

    Args:
        request: The current HTTP request.

    """
    if request.method != 'POST':
        return render(
            request,
            'metadata_upload/upload_files.html',
            {'form': UploadFileForm()},
        )

    form = UploadFileForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(
            request,
            'metadata_upload/upload_files.html',
            {'form': form},
        )

    storage = FileSystemStorage(location=settings.MEDIA_ROOT)
    seqtracking_filename = None
    seqmetadata_filename = None
    try:
        seqtracking_file = form.cleaned_data['seqtracking_file']
        seqmetadata_file = form.cleaned_data['seqmetadata_file']
        seqtracking_filename = storage.save(
            seqtracking_file.name, seqtracking_file
        )
        seqmetadata_filename = storage.save(
            seqmetadata_file.name, seqmetadata_file
        )
        upload_metadata(
            seqtracking_csv=storage.path(seqtracking_filename),
            seqmetadata_csv=storage.path(seqmetadata_filename),
            clear_all=form.cleaned_data['clear_all'],
        )
    except Exception:
        logger.exception('Metadata upload failed.')
        return render(
            request,
            'metadata_upload/upload_files.html',
            {
                'form': form,
                'error': 'Metadata upload failed. Please contact an '
                'administrator.',
            },
        )
    finally:
        if seqtracking_filename:
            storage.delete(seqtracking_filename)
        if seqmetadata_filename:
            storage.delete(seqmetadata_filename)

    return redirect('metadata_upload:upload_success')


def upload_success(request):
    """Render the successful metadata upload page.

    Args:
        request: The current HTTP request.

    """
    return render(request, 'metadata_upload/upload_success.html')
