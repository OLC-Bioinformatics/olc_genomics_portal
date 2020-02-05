# Django-related imports
from django.conf import settings
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
# Standard libraries
import os
import datetime
# Azure!
from azure.storage.blob import BlockBlobService
# Geneseekr-specific things
from olc_webportalv2.geneseekr.forms import GeneSeekrForm, TreeForm, AMRForm, ProkkaForm,EmailForm, \
    NearNeighborForm
from olc_webportalv2.geneseekr.models import GeneSeekrRequest, GeneSeekrDetail, TopBlastHit, Tree, AMRSummary, \
    AMRDetail, ProkkaRequest, NearestNeighbors, NearNeighborDetail
from olc_webportalv2.geneseekr.tasks import run_geneseekr, run_mash, run_amr_summary, run_prokka, \
    run_nearest_neighbors
from olc_webportalv2.metadata.views import id_sync


# Geneseekr Views-----------------------------------------------------------------------------------
@csrf_exempt  # needed or IE explodes
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
def geneseekr_query(request):
    form = GeneSeekrForm()
    if request.method == 'POST':
        form = GeneSeekrForm(request.POST, request.FILES)
        if form.is_valid():
            seqids, query_sequence, name = form.cleaned_data
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
            if(name):
                geneseekr_request.name = name
            geneseekr_request.save()
            run_geneseekr.apply_async(queue='geneseekr', args=(geneseekr_request.pk, ), countdown=10)

            return redirect('geneseekr:geneseekr_processing', geneseekr_request_pk=geneseekr_request.pk)

    return render(request,
                  'geneseekr/geneseekr_query.html',
                  {
                     'form': form, 
                  })


@login_required
def geneseekr_processing(request, geneseekr_request_pk):
    geneseekr_request = get_object_or_404(GeneSeekrRequest, pk=geneseekr_request_pk)
    form = EmailForm()
    if request.method == 'POST':    
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in geneseekr_request.emails_array:
                geneseekr_request.emails_array.append(email)
                geneseekr_request.save()
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(request,
                  'geneseekr/geneseekr_processing.html',
                  {
                     'geneseekr_request': geneseekr_request, 
                     'form': form
                  })


@login_required
def geneseekr_results(request, geneseekr_request_pk):
    geneseekr_request = get_object_or_404(GeneSeekrRequest, pk=geneseekr_request_pk)
    geneseekr_details = GeneSeekrDetail.objects.filter(geneseekr_request=geneseekr_request)
    id_dict = id_sync(geneseekr_request.seqids)
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
                      'idDict': id_dict,
                  })

@login_required
def geneseekr_rename(request, geneseekr_request_pk):
    geneseekr_request = get_object_or_404(GeneSeekrRequest, pk=geneseekr_request_pk)
    name = {'name': geneseekr_request.name}
    form = GeneSeekrForm(initial=name)
    if request.method == "POST": 
            geneseekr_request.name = request.POST["name"]
            geneseekr_request.save()
            return redirect('geneseekr:geneseekr_home')
        
    return render(request,
                  'rename.html',
                  {
                      'geneseekr_request': geneseekr_request,  
                      'form': form
                  })
                  
# Tree Views--------------------------------------------------------------------------------------------------
@csrf_exempt  # needed or IE explodes
@login_required
def tree_home(request):
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    tree_requests = Tree.objects.filter(user=request.user).filter(created_at__gte=one_week_ago)

    if request.method == "POST":
        if request.POST.get('delete'): 
            query = Tree.objects.filter(pk=request.POST.get('delete'))
            query.delete()
        
    return render(request,
                  'geneseekr/tree_home.html',
                  {
                      'tree_requests': tree_requests
                  })


@login_required
def tree_request(request):
    form = TreeForm()
    if request.method == 'POST':
        form = TreeForm(request.POST, request.FILES)
        if form.is_valid():
            seqids, name, number_diversitree_strains, other_files = form.cleaned_data
            tree_request = Tree.objects.create(user=request.user,
                                                       seqids=seqids)
            tree_request.status = 'Processing'
            if name is None:
                tree_request.name = tree_request.pk
            else:
                tree_request.name = name
            if number_diversitree_strains is None:
                tree_request.number_diversitree_strains = 0
            else:
                tree_request.number_diversitree_strains = number_diversitree_strains
            container_name = 'tree-{}'.format(tree_request.pk)
            file_names = list()
            for other_file in request.FILES.getlist('other_files'):
                file_name = os.path.join(container_name, other_file.name)
                file_names.append(file_name)
                blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                               account_key=settings.AZURE_ACCOUNT_KEY)
                blob_client.create_container(container_name)
                blob_client.create_blob_from_bytes(container_name=container_name,
                                                   blob_name=other_file.name,
                                                   blob=other_file.read())
            tree_request.other_input_files = file_names
            tree_request.save()
            run_mash.apply_async(queue='cowbat', args=(tree_request.pk, ), countdown=10)
            return redirect('geneseekr:tree_result', tree_request_pk=tree_request.pk)
    return render(request,
                  'geneseekr/tree_request.html',
                  {
                      'form': form
                  })


@login_required
def tree_result(request, tree_request_pk):
    tree_request = get_object_or_404(Tree, pk=tree_request_pk)
    id_dict = id_sync(tree_request.seqids)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in tree_request.emails_array:
                    tree_request.emails_array.append(email)
                    tree_request.save()
                    form = EmailForm()
                    messages.success(request, _('Email saved'))
    return render(request,
                  'geneseekr/tree_result.html',
                  {
                      'tree_request': tree_request, 
                      'form': form, 
                      'idDict': id_dict,
                  })


@login_required
def tree_rename(request, tree_request_pk):
    tree_request = get_object_or_404(Tree, pk=tree_request_pk)
    name = {'name':tree_request.name}
    form = GeneSeekrForm(initial=name)
    if request.method == "POST": 
            tree_request.name = request.POST["name"]
            tree_request.save()
            return redirect('geneseekr:tree_home')
        
    return render(request,
                  'rename.html',
                  {
                      'tree_request': tree_request,  
                      'form': form
                  })


# AMR Summary Views--------------------------------------------------------------------------------------------
@csrf_exempt  # needed or IE explodes
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
        form = AMRForm(request.POST, request.FILES)
        if form.is_valid():
            seqids, name, other_files = form.cleaned_data
            amr_request_object = AMRSummary.objects.create(user=request.user,
                                                           seqids=seqids)
            amr_request_object.status = 'Processing'
            if name is None:
                amr_request.name = amr_request.pk
            else:
                amr_request.name = name
            container_name = 'mash-{}'.format(amr_request.pk)
            file_names = list()
            for other_file in request.FILES.getlist('other_files'):
                file_name = os.path.join(container_name, other_file.name)
                file_names.append(file_name)
                blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                               account_key=settings.AZURE_ACCOUNT_KEY)
                blob_client.create_container(container_name)
                blob_client.create_blob_from_bytes(container_name=container_name,
                                                   blob_name=other_file.name,
                                                   blob=other_file.read())
            amr_request_object.other_input_files = file_names
            amr_request_object.save()
            run_amr_summary.apply_async(queue='cowbat', args=(amr_request_object.pk, ), countdown=10)
            return redirect('geneseekr:amr_result', amr_request_pk=amr_request_object.pk)
    return render(request,
                  'geneseekr/amr_request.html',
                  {
                      'form': form
                  })


@login_required
def amr_detail(request, amr_detail_pk):
    amr_detail_object = get_object_or_404(AMRDetail, pk=amr_detail_pk)
    amr_request_object = AMRSummary.objects.get(amrdetail=amr_detail)
    return render(request,
                  'geneseekr/amr_detail.html',
                  {
                      'amr_request': amr_request_object,
                      'amr_detail': amr_detail_object,
                  })


@login_required
def amr_result(request, amr_request_pk):
    amr_request_object = get_object_or_404(AMRSummary, pk=amr_request_pk)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in amr_request.emails_array:
                    amr_request_object.emails_array.append(email)
                    amr_request_object.save()
                    form = EmailForm()
                    messages.success(request, _('Email saved'))
    return render(request,
                  'geneseekr/amr_result.html',
                  {
                      'amr_request': amr_request_object,
                      'form': form,
                  })


@login_required
def amr_rename(request, amr_request_pk):
    amr_request_object = get_object_or_404(AMRSummary, pk=amr_request_pk)
    name = {'name':amr_request_object.name}
    form = AMRForm(initial=name)
    if request.method == "POST":  
        amr_request_object.name = request.POST["name"]
        amr_request_object.save()
        return redirect('geneseekr:amr_home')
    return render(request,
                  'rename.html',
                  {
                      'amr_request': amr_request_object,
                      'form': form
                  })

# Prokka Views----------------------------------------------------------------------------------------------->
@csrf_exempt  # needed or IE explodes
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
        form = ProkkaForm(request.POST, request.FILES)
        if form.is_valid():
            seqids, name, other_files = form.cleaned_data
            prokka_request_object = ProkkaRequest.objects.create(user=request.user,
                                                                 seqids=seqids,
                                                                 status='Processing')
            if name is None:
                prokka_request_object.name = prokka_request.pk
            else:
                prokka_request_object.name = name
            prokka_request_object.save()
            container_name = 'prokka-{}'.format(prokka_request_object.pk)
            blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                           account_key=settings.AZURE_ACCOUNT_KEY)
            blob_client.create_container(container_name)
            file_names = list()
            for other_file in request.FILES.getlist('other_files'):
                file_name = os.path.join(container_name, other_file.name)
                file_names.append(file_name)
                blob_client.create_blob_from_bytes(container_name=container_name,
                                                   blob_name=other_file.name,
                                                   blob=other_file.read())
            prokka_request_object.other_input_files = file_names
            prokka_request_object.save()
            run_prokka.apply_async(queue='cowbat', args=(prokka_request_object.pk, ), countdown=10)
            return redirect('geneseekr:prokka_result', prokka_request_pk=prokka_request_object.pk)
    return render(request,
                  'geneseekr/prokka_request.html',
                  {
                      'form': form
                  })


@login_required
def prokka_result(request, prokka_request_pk):
    prokka_request_object = get_object_or_404(ProkkaRequest, pk=prokka_request_pk)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in prokka_request.emails_array:
                    prokka_request_object.emails_array.append(email)
                    prokka_request_object.save()
                    form = EmailForm()
                    messages.success(request, _('Email saved'))
    return render(request,
                  'geneseekr/prokka_result.html',
                  {
                      'prokka_request': prokka_request_object,
                      'form': form,
                  })


@login_required
def prokka_rename(request, prokka_request_pk):
    prokka_request_object = get_object_or_404(ProkkaRequest, pk=prokka_request_pk)
    name = {'name':prokka_request_object.name}
    form = ProkkaForm(initial=name)
    if request.method == "POST":  
        prokka_request_object.name = request.POST["name"]
        prokka_request_object.save()
        return redirect('geneseekr:prokka_home')
        
    return render(request,
                  'rename.html',
                  {
                      'prokka_request': prokka_request_object,
                      'form': form
                  })


# NEAREST NEIGHBORS ------------------------------------------------
@login_required
def neighbor_request(request):
    form = NearNeighborForm()
    if request.method == 'POST':
        form = NearNeighborForm(request.POST, request.FILES)
        if form.is_valid():
            seqid, name, number_neighbors, uploaded_file = form.cleaned_data
            if name is None:
                name = ''
            if seqid is None:
                seqid = ''
            if uploaded_file is None:
                uploaded_file_name = ''
            else:
                uploaded_file_name = uploaded_file.name
            nearest_neighbor_task = NearestNeighbors.objects.create(seqid=seqid,
                                                                    number_neighbors=number_neighbors,
                                                                    name=name,
                                                                    user=request.user,
                                                                    uploaded_file_name=uploaded_file_name,
                                                                    status='Processing')
            nearest_neighbor_task.save()
            if uploaded_file_name != '':
                container_name = 'neighbor-{}'.format(nearest_neighbor_task.pk)
                blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                               account_key=settings.AZURE_ACCOUNT_KEY)
                blob_client.create_container(container_name)
                blob_client.create_blob_from_bytes(container_name=container_name,
                                                   blob_name=uploaded_file_name,
                                                   blob=uploaded_file.read())
            run_nearest_neighbors.apply_async(queue='geneseekr', args=(nearest_neighbor_task.pk, ), countdown=10)
            return redirect('geneseekr:neighbor_result', neighbor_request_pk=nearest_neighbor_task.pk)
    return render(request,
                  'geneseekr/neighbor_request.html',
                  {
                      'form': form
                  })


@login_required
def neighbor_result(request, neighbor_request_pk):
    neighbor_request_object = get_object_or_404(NearestNeighbors, pk=neighbor_request_pk)
    result_dict = dict()
    if neighbor_request_object.status == 'Complete':
        neighbor_details = NearNeighborDetail.objects.filter(near_neighbor_request=neighbor_request_object)
        for neighbor_detail in neighbor_details:
            result_dict[neighbor_detail.seqid] = neighbor_detail.distance
    id_dict = id_sync(result_dict.keys())
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in neighbor_request_object.emails_array:
                    neighbor_request_object.emails_array.append(email)
                    neighbor_request_object.save()
                    form = EmailForm()
                    messages.success(request, _('Email saved'))
    return render(request,
                  'geneseekr/neighbor_result.html',
                  {
                      'neighbor_request': neighbor_request_object,
                      'results': result_dict,
                      'idDict': id_dict,
                      'form': form
                  })


@csrf_exempt
@login_required
def neighbor_home(request):
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    neighbor_requests = NearestNeighbors.objects.filter(user=request.user).filter(created_at__gte=one_week_ago)

    if request.method == "POST":
        if request.POST.get('delete'):
            query = NearestNeighbors.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'geneseekr/neighbor_home.html',
                  {
                      'neighbor_requests': neighbor_requests
                  })


@login_required
def neighbor_rename(request, neighbor_request_pk):
    neighbor_request_object = get_object_or_404(NearestNeighbors, pk=neighbor_request_pk)
    name = {'name': neighbor_request_object.name}
    form = NearNeighborForm(initial=name)
    if request.method == "POST":
        neighbor_request_object.name = request.POST["name"]
        neighbor_request_object.save()
        return redirect('geneseekr:neighbor_home')

    return render(request,
                  'rename.html',
                  {
                      'neighbor_request': neighbor_request_object,
                      'form': form
                  })
