"""Views for GeneSeekr, tree, AMR, Prokka, and nearest-neighbor workflows."""

# Django-related imports
# Standard libraries
import datetime
import os

# Third-party imports
from dal import autocomplete
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from olc_webportalv2.common.storage_client import get_storage_client

# Portal-specific things
from olc_webportalv2.geneseekr.forms import (
    AMRForm,
    EmailForm,
    GeneSeekrForm,
    NameForm,
    NearNeighborForm,
    ProkkaForm,
    TreeForm,
)
from olc_webportalv2.geneseekr.models import (
    AMRDetail,
    AMRSummary,
    GeneSeekrDetail,
    GeneSeekrRequest,
    NearestNeighbors,
    NearNeighborDetail,
    ProkkaRequest,
    TopBlastHit,
    Tree,
)
from olc_webportalv2.geneseekr.tasks import (
    run_amr_summary,
    run_geneseekr,
    run_mash,
    run_nearest_neighbors,
    run_prokka,
)
from olc_webportalv2.metadata.models import Genus
from olc_webportalv2.metadata.views import id_sync


def _add_notification_email(
    analysis_request: (
        GeneSeekrRequest | Tree | AMRSummary | ProkkaRequest | NearestNeighbors
    ),
    email: str | None,
) -> bool:
    """Add an email to a request unless it is blank or already subscribed.

    Args:
        analysis_request: Request whose notification list should be updated.
        email: Address to add.

    Returns:
        Whether the request was updated.

    """
    emails = analysis_request.emails_array or []
    if not email or email in emails:
        return False

    analysis_request.emails_array = [*emails, email]
    analysis_request.save(update_fields=['emails_array'])
    return True


# GeneSeekr views
@csrf_exempt  # needed or IE explodes
@login_required
def geneseekr_home(request):
    """List recent GeneSeekr requests and handle a user's deletion request.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered GeneSeekr request list.

    """
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    geneseekr_requests = GeneSeekrRequest.objects.filter(
        user=request.user
    ).filter(created_at__gte=one_week_ago)

    if request.method == 'POST':
        if request.POST.get('delete'):
            query = GeneSeekrRequest.objects.filter(
                pk=request.POST.get('delete'),
                user=request.user,
            )
            query.delete()

    return render(
        request,
        'geneseekr/geneseekr_home.html',
        {'geneseekr_requests': geneseekr_requests},
    )


@login_required
def geneseekr_name(request, geneseekr_request_pk):
    """Rename a GeneSeekr request owned by the current user.

    Args:
        request: Incoming HTTP request.
        geneseekr_request_pk: Primary key of the request to rename.

    """
    form = NameForm()
    geneseekr_request = get_object_or_404(
        GeneSeekrRequest,
        pk=geneseekr_request_pk,
        user=request.user,
    )
    if request.method == 'POST':
        form = NameForm(request.POST)
        if form.is_valid():
            geneseekr_request.name = form.cleaned_data['name']
            geneseekr_request.save()
        return redirect('geneseekr:geneseekr_home')

    return render(
        request,
        'geneseekr/geneseekr_name.html',
        {'geneseekr_request': geneseekr_request, 'form': form},
    )


@login_required
def geneseekr_query(request):
    """Validate and enqueue a GeneSeekr analysis request.

    Args:
        request: Incoming HTTP request containing analysis input.

    """
    form = GeneSeekrForm()
    form_name = NameForm()
    if request.method == 'POST':
        form = GeneSeekrForm(request.POST, request.FILES)
        form_name = NameForm(request.POST)
        if form.is_valid():
            seqids = form.cleaned_data['seqid_list']
            query_sequence = form.cleaned_data['query_sequence']
            benchmark = form.cleaned_data['benchmark_db']

            geneseekr_request = GeneSeekrRequest.objects.create(
                user=request.user, seqids=seqids, benchmark=benchmark
            )
            # Use pasted FASTA text or read the uploaded file.
            if query_sequence != '':
                geneseekr_request.query_sequence = query_sequence
            else:
                input_sequence_file = request.FILES['query_file']
                # Reset the upload pointer before reading its contents.
                input_sequence_file.seek(0)
                input_sequence = input_sequence_file.read().decode('utf-8')
                geneseekr_request.query_sequence = input_sequence
            geneseekr_request.status = 'Processing'
            geneseekr_request.save()
            run_geneseekr.apply_async(
                queue='cowbat', args=(geneseekr_request.pk,), time_limit=600
            )
            if form_name.is_valid():
                geneseekr_request.name = form_name.cleaned_data['name']
                geneseekr_request.save()
            return redirect(
                'geneseekr:geneseekr_processing',
                geneseekr_request_pk=geneseekr_request.pk,
            )

    return render(
        request,
        'geneseekr/geneseekr_query.html',
        {
            'form': form,
            'formName': form_name,
        },
    )


@login_required
def geneseekr_processing(request, geneseekr_request_pk):
    """Show request status and save a notification email.

    Args:
        request: Incoming HTTP request.
        geneseekr_request_pk: Primary key of the GeneSeekr request.

    """
    geneseekr_request = get_object_or_404(
        GeneSeekrRequest,
        pk=geneseekr_request_pk,
        user=request.user,
    )
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if _add_notification_email(geneseekr_request, email):
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(
        request,
        'geneseekr/geneseekr_processing.html',
        {'geneseekr_request': geneseekr_request, 'form': form},
    )


@login_required
def geneseekr_results(request, geneseekr_request_pk):
    """Display GeneSeekr matches and their associated sequence metadata.

    Args:
        request: Incoming HTTP request.
        geneseekr_request_pk: Primary key of the GeneSeekr request.

    """
    geneseekr_request = get_object_or_404(
        GeneSeekrRequest,
        pk=geneseekr_request_pk,
        user=request.user,
    )
    geneseekr_details = GeneSeekrDetail.objects.filter(
        geneseekr_request=geneseekr_request
    )
    id_dict = id_sync(geneseekr_request.seqids)
    # Create dictionary where each gene gets its own top hits
    gene_top_hits = dict()
    for gene_name in geneseekr_request.gene_targets or []:
        gene_top_hits[gene_name] = TopBlastHit.objects.filter(
            geneseekr_request=geneseekr_request, gene_name=gene_name
        )
    return render(
        request,
        'geneseekr/geneseekr_results.html',
        {
            'geneseekr_request': geneseekr_request,
            'geneseekr_details': geneseekr_details,
            'gene_top_hits': gene_top_hits,
            'idDict': id_dict,
        },
    )


# Tree views
@csrf_exempt  # needed or IE explodes
@login_required
def tree_home(request):
    """List recent tree requests and handle a user's deletion request.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered tree request list.

    """
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    tree_requests = Tree.objects.filter(user=request.user).filter(
        created_at__gte=one_week_ago
    )

    if request.method == 'POST':
        if request.POST.get('delete'):
            query = Tree.objects.filter(
                pk=request.POST.get('delete'),
                user=request.user,
            )
            query.delete()

    return render(
        request, 'geneseekr/tree_home.html', {'tree_requests': tree_requests}
    )


@login_required
def tree_request(request):
    """Validate and enqueue a tree analysis request.

    Args:
        request: Incoming HTTP request containing sequence input.

    """
    form = TreeForm()
    if request.method == 'POST':
        form = TreeForm(request.POST, request.FILES)
        if form.is_valid():
            seqids = form.cleaned_data['seqid_list']
            name = form.cleaned_data.get('name')
            number_diversitree_strains = form.cleaned_data.get(
                'number_diversitree_strains'
            )
            tree_request = Tree.objects.create(
                user=request.user, seqids=seqids
            )
            tree_request.status = 'Processing'
            if name is None:
                tree_request.name = tree_request.pk
            else:
                tree_request.name = name
            if number_diversitree_strains is None:
                tree_request.number_diversitree_strains = 0
            else:
                tree_request.number_diversitree_strains = (
                    number_diversitree_strains
                )
            container_name = f'tree-{tree_request.pk}'
            file_names = list()
            for other_file in request.FILES.getlist('other_files'):
                file_name = os.path.join(container_name, other_file.name)
                file_names.append(file_name)
                storage = get_storage_client()
                storage.ensure_container(container_name)
                storage.upload_bytes(
                    container_name,
                    other_file.name,
                    other_file.read(),
                    content_type=getattr(other_file, 'content_type', None),
                )
            tree_request.other_input_files = file_names
            tree_request.save()
            run_mash.apply_async(
                queue='cowbat', args=(tree_request.pk,), countdown=10
            )
            return redirect(
                'geneseekr:tree_result', tree_request_pk=tree_request.pk
            )
    return render(request, 'geneseekr/tree_request.html', {'form': form})


@login_required
def tree_result(request, tree_request_pk):
    """Display a tree request and save a notification email.

    Args:
        request: Incoming HTTP request.
        tree_request_pk: Primary key of the tree request.

    """
    tree_request = get_object_or_404(
        Tree, pk=tree_request_pk, user=request.user
    )
    id_dict = id_sync(tree_request.seqids or [])
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if _add_notification_email(tree_request, email):
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(
        request,
        'geneseekr/tree_result.html',
        {
            'tree_request': tree_request,
            'form': form,
            'idDict': id_dict,
        },
    )


@login_required
def tree_name(request, tree_request_pk):
    """Rename a tree request owned by the current user.

    Args:
        request: Incoming HTTP request.
        tree_request_pk: Primary key of the tree request to rename.

    """
    form = NameForm()
    tree_request = get_object_or_404(
        Tree, pk=tree_request_pk, user=request.user
    )
    if request.method == 'POST':
        form = NameForm(request.POST)
        if form.is_valid():
            tree_request.name = form.cleaned_data['name']
            tree_request.save()
        return redirect('geneseekr:tree_home')

    return render(
        request,
        'geneseekr/tree_name.html',
        {'tree_request': tree_request, 'form': form},
    )


# AMR summary views
@csrf_exempt  # needed or IE explodes
@login_required
def amr_home(request):
    """List recent AMR requests and handle a user's deletion request.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered AMR request list.

    """
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    amr_requests = AMRSummary.objects.filter(user=request.user).filter(
        created_at__gte=one_week_ago
    )

    if request.method == 'POST':
        if request.POST.get('delete'):
            query = AMRSummary.objects.filter(
                pk=request.POST.get('delete'),
                user=request.user,
            )
            query.delete()

    return render(
        request, 'geneseekr/amr_home.html', {'amr_requests': amr_requests}
    )


@login_required
def amr_request(request):
    """Validate and enqueue an AMR summary request.

    Args:
        request: Incoming HTTP request containing sequence input.

    """
    form = AMRForm()
    if request.method == 'POST':
        form = AMRForm(request.POST, request.FILES)
        if form.is_valid():
            seqids = form.cleaned_data['seqid_list']
            name = form.cleaned_data.get('name')
            amr_request_object = AMRSummary.objects.create(
                user=request.user, seqids=seqids
            )
            amr_request_object.status = 'Processing'
            if name is None:
                amr_request_object.name = amr_request_object.pk
            else:
                amr_request_object.name = name
            container_name = f'amrsummary-{amr_request_object.pk}'
            file_names = list()
            for other_file in request.FILES.getlist('other_files'):
                file_name = os.path.join(container_name, other_file.name)
                file_names.append(file_name)
                storage = get_storage_client()
                storage.ensure_container(container_name)
                storage.upload_bytes(
                    container_name,
                    other_file.name,
                    other_file.read(),
                    content_type=getattr(other_file, 'content_type', None),
                )
            amr_request_object.other_input_files = file_names
            amr_request_object.save()
            run_amr_summary.apply_async(
                queue='cowbat', args=(amr_request_object.pk,), countdown=10
            )
            return redirect(
                'geneseekr:amr_result', amr_request_pk=amr_request_object.pk
            )
    return render(request, 'geneseekr/amr_request.html', {'form': form})


@login_required
def amr_detail(request, amr_detail_pk):
    """Display an AMR detail row belonging to the current user.

    Args:
        request: Incoming HTTP request.
        amr_detail_pk: Primary key of the AMR detail row.

    """
    amr_detail_object = get_object_or_404(
        AMRDetail,
        pk=amr_detail_pk,
        amr_request__user=request.user,
    )
    amr_request_object = AMRSummary.objects.get(amrdetail=amr_detail_object)
    return render(
        request,
        'geneseekr/amr_detail.html',
        {
            'amr_request': amr_request_object,
            'amr_detail': amr_detail_object,
        },
    )


@login_required
def amr_result(request, amr_request_pk):
    """Display AMR results and save a notification email.

    Args:
        request: Incoming HTTP request.
        amr_request_pk: Primary key of the AMR request.

    """
    amr_request_object = get_object_or_404(
        AMRSummary,
        pk=amr_request_pk,
        user=request.user,
    )
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if _add_notification_email(amr_request_object, email):
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(
        request,
        'geneseekr/amr_result.html',
        {
            'amr_request': amr_request_object,
            'form': form,
        },
    )


@login_required
def amr_name(request, amr_request_pk):
    """Rename an AMR request owned by the current user.

    Args:
        request: Incoming HTTP request.
        amr_request_pk: Primary key of the AMR request to rename.

    """
    form = NameForm()
    amr_request_object = get_object_or_404(
        AMRSummary,
        pk=amr_request_pk,
        user=request.user,
    )
    if request.method == 'POST':
        form = NameForm(request.POST)
        if form.is_valid():
            amr_request_object.name = form.cleaned_data['name']
            amr_request_object.save()
        return redirect('geneseekr:amr_home')
    return render(
        request,
        'geneseekr/amr_name.html',
        {'amr_request': amr_request_object, 'form': form},
    )


# Prokka views
@csrf_exempt  # needed or IE explodes
@login_required
def prokka_home(request):
    """List recent Prokka requests and handle a user's deletion request.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered Prokka request list.

    """
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    prokka_requests = ProkkaRequest.objects.filter(user=request.user).filter(
        created_at__gte=one_week_ago
    )
    if request.method == 'POST':
        if request.POST.get('delete'):
            query = ProkkaRequest.objects.filter(
                pk=request.POST.get('delete'),
                user=request.user,
            )
            query.delete()

    return render(
        request,
        'geneseekr/prokka_home.html',
        {'prokka_requests': prokka_requests},
    )


@login_required
def prokka_request(request):
    """Validate and enqueue a Prokka annotation request.

    Args:
        request: Incoming HTTP request containing sequence input.

    """
    form = ProkkaForm()
    if request.method == 'POST':
        form = ProkkaForm(request.POST, request.FILES)
        if form.is_valid():
            seqids = form.cleaned_data['seqid_list']
            name = form.cleaned_data.get('name')
            prokka_request_object = ProkkaRequest.objects.create(
                user=request.user, seqids=seqids, status='Processing'
            )
            if name is None:
                prokka_request_object.name = prokka_request_object.pk
            else:
                prokka_request_object.name = name
            prokka_request_object.save()
            container_name = f'prokka-{prokka_request_object.pk}'
            storage = get_storage_client()
            storage.ensure_container(container_name)
            file_names = list()
            for other_file in request.FILES.getlist('other_files'):
                file_name = os.path.join(container_name, other_file.name)
                file_names.append(file_name)
                storage.upload_bytes(
                    container_name,
                    other_file.name,
                    other_file.read(),
                    content_type=getattr(other_file, 'content_type', None),
                )
            prokka_request_object.other_input_files = file_names
            prokka_request_object.save()
            run_prokka.apply_async(
                queue='cowbat', args=(prokka_request_object.pk,), countdown=10
            )
            return redirect(
                'geneseekr:prokka_result',
                prokka_request_pk=prokka_request_object.pk,
            )
    return render(request, 'geneseekr/prokka_request.html', {'form': form})


@login_required
def prokka_result(request, prokka_request_pk):
    """Display Prokka request status and save a notification email.

    Args:
        request: Incoming HTTP request.
        prokka_request_pk: Primary key of the Prokka request.

    """
    prokka_request_object = get_object_or_404(
        ProkkaRequest,
        pk=prokka_request_pk,
        user=request.user,
    )
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if _add_notification_email(prokka_request_object, email):
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(
        request,
        'geneseekr/prokka_result.html',
        {
            'prokka_request': prokka_request_object,
            'form': form,
        },
    )


@login_required
def prokka_name(request, prokka_request_pk):
    """Rename a Prokka request owned by the current user.

    Args:
        request: Incoming HTTP request.
        prokka_request_pk: Primary key of the Prokka request to rename.

    """
    form = NameForm()
    prokka_request_object = get_object_or_404(
        ProkkaRequest,
        pk=prokka_request_pk,
        user=request.user,
    )
    if request.method == 'POST':
        form = NameForm(request.POST)
        if form.is_valid():
            prokka_request_object.name = form.cleaned_data['name']
            prokka_request_object.save()
        return redirect('geneseekr:prokka_home')

    return render(
        request,
        'geneseekr/prokka_name.html',
        {'prokka_request': prokka_request_object, 'form': form},
    )


# NEAREST NEIGHBORS ------------------------------------------------
@login_required
def neighbor_request(request):
    """Validate and enqueue a nearest-neighbor analysis request.

    Args:
        request: Incoming HTTP request containing the query sequence.

    """
    form = NearNeighborForm()
    if request.method == 'POST':
        form = NearNeighborForm(request.POST, request.FILES)
        if form.is_valid():
            seqid = form.cleaned_data.get('seqid')
            name = form.cleaned_data.get('name')
            number_neighbors = form.cleaned_data['number_neighbors']
            uploaded_file = form.cleaned_data.get('uploaded_file')
            if name is None:
                name = ''
            if seqid is None:
                seqid = ''
            if uploaded_file is None:
                uploaded_file_name = ''
            else:
                uploaded_file_name = uploaded_file.name
            nearest_neighbor_task = NearestNeighbors.objects.create(
                seqid=seqid,
                number_neighbors=number_neighbors,
                name=name,
                user=request.user,
                uploaded_file_name=uploaded_file_name,
                status='Processing',
            )
            nearest_neighbor_task.save()
            if uploaded_file is not None:
                container_name = f'neighbor-{nearest_neighbor_task.pk}'
                storage = get_storage_client()
                storage.ensure_container(container_name)
                storage.upload_bytes(
                    container_name,
                    uploaded_file_name,
                    uploaded_file.read(),
                    content_type=getattr(uploaded_file, 'content_type', None),
                )
            run_nearest_neighbors.apply_async(
                queue='geneseekr',
                args=(nearest_neighbor_task.pk,),
                countdown=10,
            )
            return redirect(
                'geneseekr:neighbor_result',
                neighbor_request_pk=nearest_neighbor_task.pk,
            )
    return render(request, 'geneseekr/neighbor_request.html', {'form': form})


@login_required
def neighbor_result(request, neighbor_request_pk):
    """Display nearest-neighbor results and save a notification email.

    Args:
        request: Incoming HTTP request.
        neighbor_request_pk: Primary key of the nearest-neighbor request.

    """
    neighbor_request_object = get_object_or_404(
        NearestNeighbors,
        pk=neighbor_request_pk,
        user=request.user,
    )
    result_dict = dict()
    if neighbor_request_object.status == 'Complete':
        neighbor_details = NearNeighborDetail.objects.filter(
            near_neighbor_request=neighbor_request_object
        )
        for neighbor_detail in neighbor_details:
            result_dict[neighbor_detail.seqid] = neighbor_detail.distance
    id_dict = id_sync(result_dict.keys())
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if _add_notification_email(neighbor_request_object, email):
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(
        request,
        'geneseekr/neighbor_result.html',
        {
            'neighbor_request': neighbor_request_object,
            'results': result_dict,
            'idDict': id_dict,
            'form': form,
        },
    )


@csrf_exempt
@login_required
def neighbor_home(request):
    """List recent nearest-neighbor requests and handle deletion.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered nearest-neighbor request list.

    """
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    neighbor_requests = NearestNeighbors.objects.filter(
        user=request.user
    ).filter(created_at__gte=one_week_ago)

    if request.method == 'POST':
        if request.POST.get('delete'):
            query = NearestNeighbors.objects.filter(
                pk=request.POST.get('delete'),
                user=request.user,
            )
            query.delete()

    return render(
        request,
        'geneseekr/neighbor_home.html',
        {'neighbor_requests': neighbor_requests},
    )


@login_required
def neighbor_name(request, neighbor_request_pk):
    """Rename a nearest-neighbor request owned by the current user.

    Args:
        request: Incoming HTTP request.
        neighbor_request_pk: Primary key of the request to rename.

    """
    form = NameForm()
    neighbor_request_object = get_object_or_404(
        NearestNeighbors,
        pk=neighbor_request_pk,
        user=request.user,
    )
    if request.method == 'POST':
        form = NameForm(request.POST)
        if form.is_valid():
            neighbor_request_object.name = form.cleaned_data['name']
            neighbor_request_object.save()
        return redirect('geneseekr:neighbor_home')

    return render(
        request,
        'geneseekr/neighbor_name.html',
        {'neighbor_request': neighbor_request_object, 'form': form},
    )


# Has to be its own view; can't be mixed with others
class GenusAutoCompleter(autocomplete.Select2QuerySetView):
    """Autocomplete genus names from the portal's metadata database."""

    def get_queryset(self):
        """Return distinct genera matching the submitted query."""
        query_set = Genus.objects.all()
        if self.q:
            query_set = query_set.filter(genus__icontains=self.q)
        return query_set.order_by('genus', 'pk').distinct('genus')
