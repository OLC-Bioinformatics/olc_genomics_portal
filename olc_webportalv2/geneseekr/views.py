# Django-related imports
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.contrib import messages
# Standard libraries
import datetime
# Portal-specific things
from olc_webportalv2.geneseekr.forms import GeneSeekrForm, ParsnpForm, GeneSeekrNameForm, EmailForm
from olc_webportalv2.geneseekr.models import GeneSeekrRequest, GeneSeekrDetail, TopBlastHit, ParsnpTree
from olc_webportalv2.geneseekr.tasks import run_geneseekr, run_parsnp
# Task Management
from kombu import Queue

# Create your views here.
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
                      # 'top_blast_hits': top_blast_hits
                  })


@login_required
def tree_result(request, parsnp_request_pk):
    tree_request = get_object_or_404(ParsnpTree, pk=parsnp_request_pk)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            Email = form.cleaned_data.get('email')
            if Email not in tree_request.emails_array:
                tree_request.emails_array.append(Email)
                tree_request.save()
                form = EmailForm()
                messages.success(request, 'Email saved')
            else:
                messages.error(request, 'Email has already been saved')
            
    return render(request,
                  'geneseekr/tree_result.html',
                  {
                      'tree_request': tree_request, 'form': form,
                  })


@login_required
def tree_request(request):
    form = ParsnpForm()
    if request.method == 'POST':
        form = ParsnpForm(request.POST)
        if form.is_valid():
            seqids, name = form.cleaned_data
            tree_request = ParsnpTree.objects.create(user=request.user,
                                                     seqids=seqids)
            tree_request.status = 'Processing'
            if name == None:
                tree_request.name = name
            else:
                 tree_request.name = tree_request.pk
            tree_request.save()
            run_parsnp.apply_async(queue='geneseekr', args=(tree_request.pk, ), countdown=10)
            return redirect('geneseekr:tree_result', parsnp_request_pk=tree_request.pk)
    return render(request,
                  'geneseekr/tree_request.html',
                  {
                      'form': form
                  })


@login_required
def tree_home(request):
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    tree_requests = ParsnpTree.objects.filter(user=request.user).filter(created_at__gte=one_week_ago)
    return render(request,
                  'geneseekr/tree_home.html',
                  {
                      'tree_requests': tree_requests
                  })