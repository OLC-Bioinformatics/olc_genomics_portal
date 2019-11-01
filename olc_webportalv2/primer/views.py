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
# from .tasks import run_primer_val

# Primer_val Views
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
            target = request.POST["target"]
            organism = str(target).replace('.hash', "").replace('_', " ").title().replace('Ncbi', 'NCBI').replace('Cfia', 'CFIA')
            forward_text = request.POST["forwardPrimer"]
            reverse_text = request.POST["reversePrimer"]
            mism = request.POST["mism"]
            tag = ""
            if "tag" in request.POST:
                tag = request.POST["tag"]

            forward_split = forward_text.splitlines()
            reverse_split = reverse_text.splitlines()

            for primer in range(len(forward_split)):
                forward = forward_split[primer]
                forward = forward.upper()
                forward = forward.replace(" ", "")
                forward_gc = round(MeltingTemp.TM_GC(forward, strict = False), 2)

                reverse = reverse_split[primer]
                reverse = reverse.upper()
                reverse = reverse.replace(" ", "")
                reverse_gc = round(MeltingTemp.TM_GC(forward, strict = False), 2)

# create primer request
            primer_request = PrimerVal.objects.create(target = target,
                                                      organism = organism,
                                                      forward = forward,
                                                      reverse = reverse,
                                                      forward_gc = forward_gc,
                                                      reverse_gc = reverse_gc,
                                                      mism = mism

            )
            primer_request.status = 'Processing'
            primer_request.save()
            # primer_task.apply_async(queue='', args=(primer_request_pk,), countdown=10)

        return render(request, 'primer/primer_request.html', {'form': form})

       
@login_required
def primer_rename(request, primer_request_pk)
    form = NameForm()
    primer_request = get_object_or_404(PrimerVal, pk=primer_request_pk)
    if request.method = "POST":
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