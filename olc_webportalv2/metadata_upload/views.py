import os

from django.shortcuts import render, redirect
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from .forms import UploadFileForm
from .management.commands.upload_metadata import upload_metadata


def upload_files(request):
    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            seqtracking_file = request.FILES['seqtracking_file']
            seqmetadata_file = request.FILES['seqmetadata_file']
            clear_all = form.cleaned_data['clear_all']

            # Save the uploaded files to a temporary location
            fs = FileSystemStorage(location=settings.MEDIA_ROOT)
            seqtracking_filename = fs.save(
                seqtracking_file.name, seqtracking_file)
            seqmetadata_filename = fs.save(
                seqmetadata_file.name, seqmetadata_file)

            seqtracking_path = os.path.join(
                settings.MEDIA_ROOT, seqtracking_filename)
            seqmetadata_path = os.path.join(
                settings.MEDIA_ROOT, seqmetadata_filename)

            try:
                # Call the upload_metadata function
                upload_metadata(
                    seqtracking_csv=seqtracking_path,
                    seqmetadata_csv=seqmetadata_path,
                    clear_all=clear_all)
                return redirect('metadata_upload:upload_success')
            except Exception as exc:
                return render(
                    request, 'metadata_upload/upload_files.html',
                    {'form': form, 'error': str(exc)})
        else:
            # If the form is not valid, render the form with errors
            return render(request,
                          'metadata_upload/upload_files.html',
                          {'form': form})

    else:
        form = UploadFileForm()
    return render(request, 'metadata_upload/upload_files.html', {'form': form})


def upload_success(request):
    return render(request, 'metadata_upload/upload_success.html')
