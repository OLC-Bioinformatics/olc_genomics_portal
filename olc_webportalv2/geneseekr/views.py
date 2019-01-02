from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from olc_webportalv2.geneseekr.forms import GeneSeekrForm, ParsnpForm
from olc_webportalv2.geneseekr.models import GeneSeekrRequest, GeneSeekrDetail, TopBlastHit, ParsnpTree
from olc_webportalv2.geneseekr.tasks import run_geneseekr, run_parsnp

import datetime


# Create your views here.
@login_required
def geneseekr_home(request):
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    geneseekr_requests = GeneSeekrRequest.objects.filter(user=request.user).filter(created_at__gte=one_week_ago)
    return render(request,
                  'geneseekr/geneseekr_home.html',
                  {
                      'geneseekr_requests': geneseekr_requests
                  })


@login_required
def geneseekr_query(request):
    form = GeneSeekrForm()
    if request.method == 'POST':
        form = GeneSeekrForm(request.POST, request.FILES)
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
                input_sequence = input_sequence_file.read()
                geneseekr_request.query_sequence = input_sequence
            geneseekr_request.status = 'Processing'
            geneseekr_request.save()
            run_geneseekr(geneseekr_request_pk=geneseekr_request.pk)
            return redirect('geneseekr:geneseekr_processing', geneseekr_request_pk=geneseekr_request.pk)
    return render(request,
                  'geneseekr/geneseekr_query.html',
                  {
                     'form': form
                  })


@login_required
def geneseekr_processing(request, geneseekr_request_pk):
    geneseekr_request = get_object_or_404(GeneSeekrRequest, pk=geneseekr_request_pk)
    return render(request,
                  'geneseekr/geneseekr_processing.html',
                  {
                     'geneseekr_request': geneseekr_request
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
    return render(request,
                  'geneseekr/tree_result.html',
                  {
                      'tree_request': tree_request
                  })


@login_required
def tree_request(request):
    form = ParsnpForm()
    if request.method == 'POST':
        form = ParsnpForm(request.POST)
        if form.is_valid():
            seqids = form.cleaned_data
            tree_request = ParsnpTree.objects.create(user=request.user,
                                                     seqids=seqids)
            tree_request.status = 'Processing'
            tree_request.save()
            run_parsnp(tree_request.pk)
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

