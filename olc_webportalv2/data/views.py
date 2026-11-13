"""Views for the sequence data request workflow."""

import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from olc_webportalv2.data.forms import DataRequestForm
from olc_webportalv2.data.models import DataRequest
from olc_webportalv2.data.tasks import get_assembled_data, get_raw_data


@login_required
def data_home(request: HttpRequest) -> HttpResponse:
    """Show the current user's recent sequence data requests.

    Args:
        request: The current HTTP request.

    """
    one_week_ago = datetime.date.today() - datetime.timedelta(days=7)
    data_requests = DataRequest.objects.filter(
        user=request.user,
        created_at__gte=one_week_ago,
    )
    return render(
        request,
        'data/data_home.html',
        {'data_requests': data_requests},
    )


@login_required
def assembled_data(request: HttpRequest) -> HttpResponse:
    """Create a request for processed FASTA files.

    Args:
        request: The current HTTP request.

    """
    form = DataRequestForm()
    if request.method == 'POST':
        form = DataRequestForm(request.POST)
        if form.is_valid():
            seqids = form.cleaned_data['seqids'].split()
            data_request = DataRequest.objects.create(
                seqids=seqids,
                user=request.user,
                status='Processing',
                request_type='FASTA',
            )
            get_assembled_data.apply_async(
                queue='cowbat',
                args=(data_request.pk,),
                countdown=10,
            )
            return redirect(
                'data:data_download', data_request_pk=data_request.pk
            )
    return render(request, 'data/assembled_data.html', {'form': form})


@login_required
def data_download(
    request: HttpRequest,
    data_request_pk: int,
) -> HttpResponse:
    """Display a data request owned by the current user.

    Args:
        request: The current HTTP request.
        data_request_pk: Primary key of the requested data job.

    """
    data_request = get_object_or_404(
        DataRequest,
        pk=data_request_pk,
        user=request.user,
    )
    return render(
        request,
        'data/data_download.html',
        {'data_request': data_request},
    )


@login_required
def raw_data(request: HttpRequest) -> HttpResponse:
    """Create a request for raw FASTQ files.

    Args:
        request: The current HTTP request.

    """
    form = DataRequestForm()
    if request.method == 'POST':
        form = DataRequestForm(request.POST)
        if form.is_valid():
            seqids = form.cleaned_data['seqids'].split()
            data_request = DataRequest.objects.create(
                seqids=seqids,
                user=request.user,
                status='Processing',
                request_type='FASTQ',
            )
            get_raw_data.apply_async(
                queue='cowbat',
                args=(data_request.pk,),
                countdown=10,
            )
            return redirect(
                'data:data_download', data_request_pk=data_request.pk
            )
    return render(request, 'data/raw_data.html', {'form': form})
