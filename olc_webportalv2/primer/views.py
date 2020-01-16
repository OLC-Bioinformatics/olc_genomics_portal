from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from azure.storage.blob import BlockBlobService
from django.contrib import messages
from weasyprint import HTML, CSS
from django.conf import settings

from Bio.SeqUtils import MeltingTemp

# Primer_Val-specific code
from .forms import PrimerForm
from .models import PrimerVal
from .tasks import run_primer_val

# Primer_Val Views
@csrf_exempt  # needed or IE explodes
@login_required
def primer_home(request):
    primer_requests = PrimerVal.objects.filter(user=request.user)

    if request.method == "POST":
        if request.POST.get('delete'): 
            query = PrimerVal.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'primer/primer_home.html',
                  {
                      'primer_requests': primer_requests
                  })


@login_required
def primer_request(request):
    form = PrimerForm()
    if request.method == "POST":
        form = PrimerForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data.get('name')
            path = form.cleaned_data.get('path')
            sequence_path = form.cleaned_data.get('sequence_path')
            primer_file = form.cleaned_data.get('primer_file')
            mismatches = form.cleaned_data.get('mismatches')
            analysistype = form.cleaned_data.get('analysistype')
            
            # create primer request
            primer_request = PrimerVal.objects.create(name = name,
                                                      path = path,
                                                      sequence_path = sequence_path,
                                                      primer_file = primer_file,
                                                      mismatches = mismatches,
                                                      analysistype = analysistype)

            
            primer_request.status = 'Processing'
            primer_request.save()
            run_primer_val.apply_async(queue='default', args=(primer_request_pk,), countdown=10)

        return render(request, 'primer/primer_request.html', {'form': form})


@login_required
def primer_upload(request, primer_request_pk):
    primer_request = get_object_or_404(PrimerVal, pk=primer_request_pk)
    if request.method == 'POST':
        seq_files = [request.FILES.get('file[%d]' % i) for i in range(0, len(request.FILES))]
        if seq_files:
            container_name = PrimerVal.objects.get(pk=primer_request_pk).container_namer()
            blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                           account_key=settings.AZURE_ACCOUNT_KEY)
            blob_client.create_container(container_name)
            primer_request.status = 'Processing'
            primer_request.save()
            run_primer_val.apply_async(queue='default', args=(primer_request_pk,), countdown=10)
        return redirect('primer:primer_home')
    return render(request,
                  'primer/primer_upload.html',
                  {
                      'primer_request': primer_request,
                  })



       
@login_required
def primer_rename(request, primer_request_pk):
    form = NameForm()
    primer_request = get_object_or_404(PrimerVal, pk=primer_request_pk)
    if request.method == "POST":
        form = NameForm(request.POST)
        if form.is_valid():
            primer_request.name = form.cleaned_data['name']
            primer_request.save()
        return redirect('primer/primer_home.html')

    return render(request,
                  'primer/primer_name.html',
                  {
                      'primer_request': primer_request,  
                      'form': form
                  })