"""Views and API endpoints for the metadata catalog."""

from dal import autocomplete
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from rest_framework import (
    generics,
    pagination,
    permissions,
)

from olc_webportalv2.common.storage_client import get_storage_client
from olc_webportalv2.metadata.forms import (
    MetaDataRequestForm,
    make_genus_choice_list,
    make_mlst_choice_list,
    make_rmlst_choice_list,
    make_serotype_choice_list,
    make_species_choice_list,
)
from olc_webportalv2.metadata.models import (
    OLNID,
    Genus,
    MetaDataRequest,
    SequenceData,
)
from olc_webportalv2.metadata.serializers import (
    OLNSerializer,
    SequenceDataSerializer,
)


# Not sure where to put this - create pagination.py?
class LargeResultsPagination(pagination.PageNumberPagination):
    """Paginate metadata API results with a configurable page size."""

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 20000


class OLNList(generics.ListCreateAPIView):
    """List OLC network identifiers."""

    queryset = OLNID.objects.all()
    serializer_class = OLNSerializer
    pagination_class = LargeResultsPagination


class OLNDetail(generics.RetrieveAPIView):
    """Return sequence quality values for one OLC network identifier."""

    queryset = OLNID.objects.all()
    serializer_class = OLNSerializer
    permission_classes = (permissions.AllowAny,)

    def retrieve(self, request, *args, **kwargs):
        """Return sequence IDs and quality for the requested OLN.

        Args:
            request: The current API request.
            args: Additional positional view arguments.
            kwargs: View arguments containing the OLN identifier.

        """
        oln = get_object_or_404(
            self.get_queryset(),
            olnid=kwargs['oln_id'],
        )
        seqids = SequenceData.objects.filter(olnid=oln)
        seqdata_dict = dict()
        for seqid in seqids:
            seqdata_dict[seqid.seqid] = seqid.quality
        return JsonResponse(seqdata_dict)


class SequenceDataList(generics.ListCreateAPIView):
    """List and create sequence metadata records for administrators."""

    queryset = SequenceData.objects.all()
    serializer_class = SequenceDataSerializer
    permission_classes = (permissions.IsAdminUser,)
    pagination_class = LargeResultsPagination


class SequenceDataDetail(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update, or delete sequence metadata records."""

    queryset = SequenceData.objects.all()
    serializer_class = SequenceDataSerializer
    permission_classes = (permissions.IsAdminUser,)

    def retrieve(self, request, *args, **kwargs):
        """Return a temporary download URL for a sequence.

        Args:
            request: The current API request.
            args: Additional positional view arguments.
            kwargs: View arguments containing the record primary key.

        """
        sequence_data = self.get_object()
        seqid = sequence_data.seqid
        sas_url = get_storage_client().create_transfer_url(
            'processed-data',
            'download',
            name=seqid + '.fasta',
            expires_in_seconds=3600,
        )
        return JsonResponse(
            {
                'id': sequence_data.pk,
                'seqid': seqid,
                'download_link': sas_url,
            }
        )


class GenusAutoCompleter(autocomplete.Select2ListView):
    """Provide genus choices for the metadata search form."""

    def get_list(self):
        """Return current genus values."""
        return make_genus_choice_list()


class GenusQueryAutoCompleter(autocomplete.Select2QuerySetView):
    """Search genus records for the richer autocomplete widget."""

    def get_queryset(self):
        """Return matching genera to authenticated users."""
        if not self.request.user.is_authenticated:
            return Genus.objects.none()
        queryset = Genus.objects.all()
        if self.q:
            queryset = queryset.filter(genus__icontains=self.q)
        return queryset.order_by('genus')


class SeqIDAutoCompleter(autocomplete.Select2QuerySetView):
    """Search sequence IDs for the autocomplete widget."""

    def get_queryset(self):
        """Return matching sequence records to authenticated users."""
        if not self.request.user.is_authenticated:
            return SequenceData.objects.none()
        queryset = SequenceData.objects.all()
        if self.q:
            queryset = queryset.filter(seqid__icontains=self.q)
        return queryset.order_by('seqid')


class SpeciesAutoCompleter(autocomplete.Select2ListView):
    """Provide species choices for the metadata search form."""

    def get_list(self):
        """Return current species values."""
        return make_species_choice_list()


class SerotypeAutoCompleter(autocomplete.Select2ListView):
    """Provide serotype choices for the metadata search form."""

    def get_list(self):
        """Return current serotype values."""
        return make_serotype_choice_list()


class MLSTAutoCompleter(autocomplete.Select2ListView):
    """Provide MLST choices for the metadata search form."""

    def get_list(self):
        """Return current MLST values."""
        return make_mlst_choice_list()


class RMLSTAutoCompleter(autocomplete.Select2ListView):
    """Provide rMLST choices for the metadata search form."""

    def get_list(self):
        """Return current rMLST values."""
        return make_rmlst_choice_list()


@login_required
def metadata_home(request):
    """Filter the sequence catalog and save the selected IDs.

    Args:
        request: The current HTTP request.

    """
    form = MetaDataRequestForm()
    if request.method == 'POST':
        form = MetaDataRequestForm(request.POST)
        if form.is_valid():
            quality = form.cleaned_data.get('quality')
            genus = form.cleaned_data.get('genus')
            species = form.cleaned_data.get('species')
            serotype = form.cleaned_data.get('serotype')
            mlst = form.cleaned_data.get('mlst')
            rmlst = form.cleaned_data.get('rmlst')
            # exclude = form.cleaned_data.get('everything_but')
            sequence_data_matching_query = SequenceData.objects.all()
            criteria_dict = {}
            # Filter based on each possible criterion, if user put anything.
            if species:
                sequence_data_matching_query = (
                    sequence_data_matching_query.filter(
                        species__iexact=species
                    )
                )
                criteria_dict['species'] = species
            if serotype:
                sequence_data_matching_query = (
                    sequence_data_matching_query.filter(
                        serotype__iexact=serotype
                    )
                )
                criteria_dict['serotype'] = serotype
            if mlst:
                sequence_data_matching_query = (
                    sequence_data_matching_query.filter(mlst__iexact=mlst)
                )
                criteria_dict['mlst'] = mlst
            if rmlst:
                sequence_data_matching_query = (
                    sequence_data_matching_query.filter(rmlst__iexact=rmlst)
                )
                criteria_dict['rmlst'] = rmlst
            if genus:
                sequence_data_matching_query = (
                    sequence_data_matching_query.filter(genus__iexact=genus)
                )
                criteria_dict['genus'] = genus

            pr = _('Pass + Reference')
            r = _('Reference')
            a = _('All')

            # Deal with quality.
            if quality == _('Pass'):
                sequence_data_matching_query = (
                    sequence_data_matching_query.exclude(quality='Fail')
                )
                criteria_dict['quality'] = pr
            elif quality == _('Reference'):
                sequence_data_matching_query = (
                    sequence_data_matching_query.filter(quality='Reference')
                )
                criteria_dict['quality'] = r
            else:
                criteria_dict['quality'] = a

            seqid_list = list(
                sequence_data_matching_query.values_list('seqid', flat=True)
            )

            metadata_request = MetaDataRequest.objects.create(
                seqids=seqid_list, criteria=criteria_dict
            )
            return redirect(
                'metadata:metadata_results',
                metadata_request_pk=metadata_request.pk,
            )
    return render(request, 'metadata/metadata_home.html', {'form': form})


@login_required
def metadata_results(request, metadata_request_pk):
    """Display saved sequence IDs and their associated identifiers.

    Args:
        request: The current HTTP request.
        metadata_request_pk: Primary key of the saved metadata search.

    """
    metadata_result = get_object_or_404(
        MetaDataRequest, pk=metadata_request_pk
    )
    id_dict = id_sync(metadata_result.seqids)
    id_list = ' '.join(id_dict)
    return render(
        request,
        'metadata/metadata_results.html',
        {
            'metadata_result': metadata_result,
            'idDict': id_dict,
            'idList': id_list,
        },
    )


@login_required
def metadata_browse(request):
    """Display sequence metadata with laboratory identifiers.

    Args:
        request: The current HTTP request.

    """
    sequence_data = SequenceData.objects.filter().select_related('labid')
    return render(
        request,
        'metadata/metadata_browse.html',
        {'sequence_data': sequence_data},
    )


def id_sync(seqids):
    """Map sequence IDs to their laboratory and network identifiers.

    Args:
        seqids: Sequence identifiers to look up.

    Returns:
        A mapping from sequence ID to LabID and OLNID values.

    """
    sequence_data = SequenceData.objects.filter(
        seqid__in=seqids
    ).select_related('labid', 'olnid')
    return {
        record.seqid: (
            str(record.labid) if record.labid else 'N/A',
            str(record.olnid) if record.olnid else 'N/A',
        )
        for record in sequence_data
    }


@login_required
def metadata_submit(request):
    """Render the static WGS isolate submission form links.

    Args:
        request: The current HTTP request.

    """
    return render(
        request,
        'metadata/metadata_submit.html',
    )
