# Django-related imports
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
# Standard libraries
import datetime
# Portal-specific things
from olc_webportalv2.geneseekr.forms import GeneSeekrForm, ParsnpForm, AMRForm, ProkkaForm, GeneSeekrNameForm, TreeNameForm, EmailForm
from olc_webportalv2.geneseekr.models import GeneSeekrRequest, GeneSeekrDetail, TopBlastHit, ParsnpTree, AMRSummary, AMRDetail, ProkkaRequest
from olc_webportalv2.geneseekr.tasks import run_geneseekr, run_parsnp, run_amr_summary, run_prokka
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.metadata.views import LabID_sync_SeqID
# Task Management
from kombu import Queue

# Geneseekr Views------------------------------------------------------------------------------------------------------------------------------>
@csrf_exempt #needed or IE explodes
@login_required
def geneseekr_home(request):
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    geneseekr_requests = GeneSeekrRequest.objects.filter(user=request.user).filter(created_at__gte=one_week_ago)

    if request.method == "POST":
        if request.POST.get('delete'): 
            query = GeneSeekrRequest.objects.filter(pk=request.POST.get('delete'))
            query.delete()
        
    return render(request,
                  'geneseekr/geneseekr_home.html',
                  {
                      'geneseekr_requests': geneseekr_requests
                  })

@login_required
def geneseekr_name(request, geneseekr_request_pk):
    form = GeneSeekrNameForm()
    geneseekr_request = get_object_or_404(GeneSeekrRequest, pk=geneseekr_request_pk)
    if request.method == "POST":  
        form = GeneSeekrNameForm(request.POST)
        if form.is_valid():
            geneseekr_request.name = form.cleaned_data['name']
            geneseekr_request.save()
        return redirect('geneseekr:geneseekr_home')
        
    return render(request,
                  'geneseekr/geneseekr_name.html',
                  {
                      'geneseekr_request': geneseekr_request,  'form': form
                  })
                  
@login_required
def geneseekr_query(request):
    form = GeneSeekrForm()
    formName = GeneSeekrNameForm()
    formEmail = EmailForm()
    if request.method == 'POST':
        form = GeneSeekrForm(request.POST, request.FILES)
        formName = GeneSeekrNameForm(request.POST)
        if form.is_valid():
            # seqid_input = form.cleaned_data.get('seqids')
            # seqids = seqid_input.split()
            seqids, query_sequence = form.cleaned_data
            geneseekr_request = GeneSeekrRequest.objects.create(user=request.user,
                                                                seqids=seqids)
            # Use query sequence if entered. Otherwise, read in the FASTA file provided.
            if query_sequence != '':
                geneseekr_request.query_sequence = query_sequence
            else:
                input_sequence_file = request.FILES['query_file']
                # Pointer is at end of file in request, so move back to beginning before doing the read.
                input_sequence_file.seek(0)
                input_sequence = input_sequence_file.read().decode('utf-8')
                geneseekr_request.query_sequence = input_sequence
            geneseekr_request.status = 'Processing'
            geneseekr_request.save()
            run_geneseekr.apply_async(queue='geneseekr', args=(geneseekr_request.pk, ), countdown=10)
            if formName.is_valid():
                geneseekr_request.name = formName.cleaned_data['name']
                geneseekr_request.save()
            return redirect('geneseekr:geneseekr_processing', geneseekr_request_pk=geneseekr_request.pk)

    return render(request,
                  'geneseekr/geneseekr_query.html',
                  {
                     'form': form, 'formName':formName,
                  })

@login_required
def geneseekr_processing(request, geneseekr_request_pk):
    geneseekr_request = get_object_or_404(GeneSeekrRequest, pk=geneseekr_request_pk)

    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            Email = form.cleaned_data.get('email')
            if Email not in geneseekr_request.emails_array:
                geneseekr_request.emails_array.append(Email)
                geneseekr_request.save()
                form = EmailForm()
                messages.success(request, 'Email saved')
            else:
                messages.error(request, 'Email has already been saved')
    return render(request,
                  'geneseekr/geneseekr_processing.html',
                  {
                     'geneseekr_request': geneseekr_request, "form": form
                  })

@login_required
def geneseekr_results(request, geneseekr_request_pk):
    geneseekr_request = get_object_or_404(GeneSeekrRequest, pk=geneseekr_request_pk)
    geneseekr_details = GeneSeekrDetail.objects.filter(geneseekr_request=geneseekr_request)
    labidDict = LabID_sync_SeqID(geneseekr_request.seqids)
    # Create dictionary where each gene gets its own top hits
    gene_top_hits = dict()
    for gene_name in geneseekr_request.gene_targets:
        gene_top_hits[gene_name] = TopBlastHit.objects.filter(geneseekr_request=geneseekr_request, gene_name=gene_name)
    return render(request,
                  'geneseekr/geneseekr_results.html',
                  {
                      'geneseekr_request': geneseekr_request,
                      'geneseekr_details': geneseekr_details,
                      'gene_top_hits': gene_top_hits,
                      'labidDict': labidDict,
                      # 'top_blast_hits': top_blast_hits
                  })

# Tree Views------------------------------------------------------------------------------------------------------------------------------>
@csrf_exempt #needed or IE explodes
@login_required
def tree_home(request):
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    parsnp_requests = ParsnpTree.objects.filter(user=request.user).filter(created_at__gte=one_week_ago)

    if request.method == "POST":
        if request.POST.get('delete'): 
            query = ParsnpTree.objects.filter(pk=request.POST.get('delete'))
            query.delete()
        

    return render(request,
                  'geneseekr/tree_home.html',
                  {
                      'parsnp_requests': parsnp_requests
                  })

@login_required
def tree_request(request):
    form = ParsnpForm()
    if request.method == 'POST':
        form = ParsnpForm(request.POST)
        if form.is_valid():
            seqids, name, tree_program, number_diversitree_strains  = form.cleaned_data
            parsnp_request = ParsnpTree.objects.create(user=request.user,
                                                     seqids=seqids)
            parsnp_request.status = 'Processing'
            if name == None:
                parsnp_request.name = parsnp_request.pk
            else:
                parsnp_request.name = name
            if number_diversitree_strains == None:
                parsnp_request.number_diversitree_strains = 0
            else:
                parsnp_request.number_diversitree_strains = number_diversitree_strains
            parsnp_request.tree_program = tree_program
            parsnp_request.save()
            run_parsnp.apply_async(queue='geneseekr', args=(parsnp_request.pk, ), countdown=10)
            return redirect('geneseekr:tree_result', parsnp_request_pk=parsnp_request.pk)
    return render(request,
                  'geneseekr/tree_request.html',
                  {
                      'form': form
                  })

@login_required
def tree_result(request, parsnp_request_pk):
    parsnp_request = get_object_or_404(ParsnpTree, pk=parsnp_request_pk)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            Email = form.cleaned_data.get('email')
            if Email not in parsnp_request.emails_array:
                parsnp_request.emails_array.append(Email)
                parsnp_request.save()
                form = EmailForm()
                messages.success(request, 'Email saved')
            else:
                messages.error(request, 'Email has already been saved')
            
    return render(request,
                  'geneseekr/tree_result.html',
                  {
                      'parsnp_request': parsnp_request, 'form': form,
                  })

@login_required
def tree_name(request, parsnp_request_pk):
    form = TreeNameForm()
    parsnp_request = get_object_or_404(ParsnpTree, pk=parsnp_request_pk)
    if request.method == "POST":  
        form = TreeNameForm(request.POST)
        if form.is_valid():
            parsnp_request.name = form.cleaned_data['name']
            parsnp_request.save()
        return redirect('geneseekr:tree_home')
        
    return render(request,
                  'geneseekr/tree_name.html',
                  {
                      'parsnp_request': parsnp_request,  'form': form
                  })

# AMR Summary Views--------------------------------------------------------------------------------------------
@csrf_exempt #needed or IE explodes
@login_required
def amr_home(request):
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    amr_requests = AMRSummary.objects.filter(user=request.user).filter(created_at__gte=one_week_ago)

    if request.method == "POST":
        if request.POST.get('delete'): 
            query = AMRSummary.objects.filter(pk=request.POST.get('delete'))
            query.delete()
        
    return render(request,
                  'geneseekr/amr_home.html',
                  {
                      'amr_requests': amr_requests
                  })

@login_required
def amr_request(request):
    form = AMRForm()
    if request.method == 'POST':
        form = AMRForm(request.POST)
        if form.is_valid():
            seqids, name = form.cleaned_data
            amr_request = AMRSummary.objects.create(user=request.user,
                                                    seqids=seqids)
            amr_request.status = 'Processing'
            if name == None:
                amr_request.name = amr_request.pk
            else:
                amr_request.name = name
            amr_request.save()
            run_amr_summary.apply_async(queue='geneseekr', args=(amr_request.pk, ), countdown=10)
            return redirect('geneseekr:amr_result', amr_request_pk=amr_request.pk)
    return render(request,
                  'geneseekr/amr_request.html',
                  {
                      'form': form
                  })

@login_required
def amr_result(request, amr_request_pk):
    amr_request = get_object_or_404(AMRSummary, pk=amr_request_pk)
    amr_details = AMRDetail.objects.filter(amr_request=amr_request)
    form = EmailForm()
    selectedSeq = None
    labidDict = LabID_sync_SeqID(amr_request.seqids)
    if request.method == 'POST':
        if 'selectedSeq' in request.POST:
            selectedSeq = request.POST.get('selectedSeq')
        else:    
            form = EmailForm(request.POST)
            if form.is_valid():
                Email = form.cleaned_data.get('email')
                if Email not in amr_request.emails_array:
                    amr_request.emails_array.append(Email)
                    amr_request.save()
                    form = EmailForm()
                    messages.success(request, 'Email saved')
                else:
                    messages.error(request, 'Email has already been saved')
            
    return render(request,
                  'geneseekr/amr_result.html',
                  {
                      'amr_request': amr_request,'amr_details': amr_details, 'form': form, 'selectedSeq':selectedSeq, 'labidDict':labidDict,
                  })

@login_required
def amr_name(request, amr_request_pk):
    form = TreeNameForm()
    amr_request = get_object_or_404(AMRSummary, pk=amr_request_pk)
    if request.method == "POST":  
        form = TreeNameForm(request.POST)
        if form.is_valid():
            amr_request.name = form.cleaned_data['name']
            amr_request.save()
        return redirect('geneseekr:amr_home')
        
    return render(request,
                  'geneseekr/amr_name.html',
                  {
                      'amr_request': amr_request,  'form': form
                  })


# Prokka Views----------------------------------------------------------------------------------------------->
@csrf_exempt #needed or IE explodes
@login_required
def prokka_home(request):
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    prokka_requests = ProkkaRequest.objects.filter(user=request.user).filter(created_at__gte=one_week_ago)

    if request.method == "POST":
        if request.POST.get('delete'): 
            query = ProkkaRequest.objects.filter(pk=request.POST.get('delete'))
            query.delete()
        
    return render(request,
                  'geneseekr/prokka_home.html',
                  {
                      'prokka_requests': prokka_requests
                  })

@login_required
def prokka_request(request):
    form = ProkkaForm()
    if request.method == 'POST':
        form = ProkkaForm(request.POST)
        if form.is_valid():
            seqids, name = form.cleaned_data
            prokka_request = ProkkaRequest.objects.create(user=request.user,
                                                    seqids=seqids)
            prokka_request.status = 'Processing'
            if name == None:
                prokka_request.name = prokka_request.pk
            else:
                prokka_request.name = name
            prokka_request.save()
            run_prokka.apply_async(queue='geneseekr', args=(prokka_request.pk, ), countdown=10)
            return redirect('geneseekr:prokka_result', prokka_request_pk=prokka_request.pk)
    return render(request,
                  'geneseekr/prokka_request.html',
                  {
                      'form': form
                  })

@login_required
def prokka_result(request, prokka_request_pk):
    prokka_request = get_object_or_404(ProkkaRequest, pk=prokka_request_pk)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            Email = form.cleaned_data.get('email')
            if Email not in prokka_request.emails_array:
                prokka_request.emails_array.append(Email)
                prokka_request.save()
                form = EmailForm()
                messages.success(request, 'Email saved')
            else:
                messages.error(request, 'Email has already been saved')
        
    return render(request,
                  'geneseekr/prokka_result.html',
                  {
                      'prokka_request': prokka_request, 'form': form,
                  })

@login_required
def prokka_name(request, prokka_request_pk):
    form = TreeNameForm()
    prokka_request = get_object_or_404(ProkkaRequest, pk=prokka_request_pk)
    if request.method == "POST":  
        form = TreeNameForm(request.POST)
        if form.is_valid():
            prokka_request.name = form.cleaned_data['name']
            prokka_request.save()
        return redirect('geneseekr:prokka_home')
        
    return render(request,
                  'geneseekr/prokka_name.html',
                  {
                      'prokka_request': prokka_request,  'form': form
                  })

