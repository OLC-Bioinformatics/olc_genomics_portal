# Django-related imports
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
# Azure!
from azure.storage.blob import BlockBlobService
# Useful things!
from weasyprint import HTML, CSS
from sentry_sdk import capture_exception
# Primer-specific code
from .forms import PrimerForm
from .models import PrimerFinder
from .tasks import run_primer_finder


# Primer_Finder Views
@csrf_exempt  # needed or IE explodes
@login_required
def primer_home(request):
    primer_requests = PrimerFinder.objects.filter(user=request.user)

    if request.method == "POST":
        if request.POST.get('delete'): 
            query = PrimerFinder.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'primer_finder/primer_home.html',
                  {
                      'primer_requests': primer_requests
                  })


@login_required
def primer_request(request):
    form = PrimerForm()
    if request.method == "POST":
        form = PrimerForm(request.POST, request.FILES)
        if form.is_valid():
            name = form.cleaned_data.get('name')
            analysistype = form.cleaned_data.get('analysistype')
            mismatches = form.cleaned_data.get('mismatches')
            if analysistype == "custom":
                # catches if primerfile is empty on Custome EPCR request
                if len(request.FILES) != 0:
                    primer_file = request.FILES['primer_file']
                else:
                    error = _('Primer file is required')
                    messages.error(request,error)
                ampliconsize = form.cleaned_data.get('ampliconsize')
                export_amplicons = form.cleaned_data.get('export_amplicons')
                # create primer request for custom
                try:
                    primer_request = PrimerFinder.objects.create(name = name,
                                                        user = request.user,
                                                        analysistype = analysistype,
                                                        mismatches = mismatches,
                                                        primer_file = primer_file,
                                                        ampliconsize = ampliconsize,
                                                        export_amplicons = export_amplicons)
                    primer_request.save()
                    container_name = PrimerFinder.objects.get(pk=primer_request_pk).container_namer()
                    blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,account_key=settings.AZURE_ACCOUNT_KEY)
                    blob_client.create_container(container_name)
                    blob_client.create_blob_from_bytes(container_name=container_name,blob_name=primer_request.analysistype,blob=primer_request.analysistype)
                    blob_client.create_blob_from_bytes(container_name=container_name,blob_name=primer_request.mismatches,blob=primer_request.mismatches)
                    blob_client.create_blob_from_bytes(container_name=container_name,blob_name=primer_request.primer_file,blob=primer_request.primer_file.read())
                    blob_client.create_blob_from_bytes(container_name=container_name,blob_name=primer_request.ampliconsize,blob=primer_request.ampliconsize)
                    blob_client.create_blob_from_bytes(container_name=container_name,blob_name=primer_request.export_amplicons,blob=primer_request.export_amplicons)
                    run_primer_finder.apply_async(queue='default', args=(primer_request_pk,), countdown=10)
                    return redirect('primer_finder:primer_results', primer_request_pk=primer_request.pk)
                except Exception as e:
                    capture_exception(e)

            else:
                # create primer request vtyper
                try:
                    primer_request = PrimerFinder.objects.create(user = request.user,
                                                        name = name,
                                                        analysistype = analysistype,
                                                        mismatches = mismatches,
                                                        )
                    primer_request.save()
                    container_name = PrimerFinder.objects.get(pk=primer_request_pk).container_namer()
                    blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,account_key=settings.AZURE_ACCOUNT_KEY)
                    blob_client.create_container(container_name)
                    blob_client.create_blob_from_bytes(container_name=container_name,blob_name=primer_request.analysistype,blob=primer_request.analysistype)
                    blob_client.create_blob_from_bytes(container_name=container_name,blob_name=primer_request.mismatches,blob=primer_request.mismatches)
                    run_primer_finder.apply_async(queue='default', args=(primer_request_pk,), countdown=10)
                    return redirect('primer_finder:primer_results', primer_request_pk=primer_request.pk)
                except Exception as e:
                    capture_exception(e)

        else:
            messages.error(request,form.errors)       
    return render(request,
        'primer_finder/primer_request.html', 
        {
            'form': form,
        })


@login_required
def primer_results(request, primer_request_pk):
    primer_request = get_object_or_404(PrimerFinder, pk=primer_request_pk)
    return render(request,
                  'primer_finder/primer_results.html',
                  {
                      'primer_request': primer_request, 
                  })

       
@login_required
def primer_rename(request, primer_request_pk):
    primer_request = get_object_or_404(PrimerFinder, pk=primer_request_pk)
    form = PrimerForm(instance=primer_request)
    if request.method == "POST":
        primer_request.name = request.POST["name"]
        primer_request.save()
        return redirect('primer_finder:primer_home')

    return render(request,
                  'rename.html',
                  {
                      'primer_request': primer_request,  
                      'form': form
                  })