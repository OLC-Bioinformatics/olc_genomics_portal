"""Views and autocomplete endpoints for Sequence Database."""

import datetime

from dal import autocomplete
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.forms.formsets import formset_factory
from django.http import JsonResponse
from django.shortcuts import render
from django_tables2.config import RequestConfig
from django_tables2.paginators import LazyPaginator
from rest_framework import generics, pagination, permissions

from olc_webportalv2.common.storage_client import get_storage_client
from olc_webportalv2.sequence_database.forms import (
    DatabaseDateForm,
    DatabaseFieldForm,
    DatabaseIDsForm,
    DatabaseRequestForm,
    SequenceDatabaseBaseFormSet,
)
from olc_webportalv2.sequence_database.models import (
    OLNID,
    DatabaseRequest,
    LookupTable,
    SequenceData,
    UniqueGenus,
    UniqueMLST,
    UniqueMLSTCC,
    UniqueRMLST,
    UniqueSerovar,
    UniqueSpecies,
)
from olc_webportalv2.sequence_database.serializers import (
    OLNSerializer,
    SequenceDataSerializer,
)
from olc_webportalv2.sequence_database.tables import SequenceDataTable


class LargeResultsPagination(pagination.PageNumberPagination):
    """Provide configurable pagination for large database API results."""

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 20000


class OLNList(generics.ListCreateAPIView):
    """List and create OLC network identifiers."""

    queryset = OLNID.objects.all()
    serializer_class = OLNSerializer
    pagination_class = LargeResultsPagination


class OLNDetail(generics.RetrieveAPIView):
    """Retrieve sequences associated with one OLC network identifier."""

    serializer_class = OLNSerializer
    permission_classes = (permissions.AllowAny,)

    def get_queryset(self):
        """Return the requested OLC network identifier."""
        oln_id = self.kwargs['oln_id']
        oln = OLNID.objects.get(olnid=oln_id)
        return oln

    def retrieve(self, request, *args, **kwargs):
        """Return sequence quality values for the requested identifier.

        Args:
            request: The current API request.
            args: Additional positional view arguments.
            kwargs: View arguments containing the OLC network identifier.
        """
        oln = self.get_queryset()
        seqids = SequenceData.objects.filter(olnid=oln)
        seqdata_dict = dict()
        for seqid in seqids:
            seqdata_dict[seqid.seqid] = seqid.quality
        return JsonResponse(seqdata_dict)

    def handle_exception(self, exc):
        """Return API exceptions as JSON error responses.

        Args:
            exc: The exception raised while handling the request.
        """
        return JsonResponse({'ERROR': str(exc)})


class SequenceDataList(generics.ListCreateAPIView):
    """List and create sequence metadata records."""

    queryset = SequenceData.objects.all()
    serializer_class = SequenceDataSerializer
    permission_classes = (permissions.IsAdminUser,)
    pagination_class = LargeResultsPagination


class SequenceDataDetail(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update, or delete an individual sequence record."""

    queryset = SequenceData.objects.all()
    serializer_class = SequenceDataSerializer
    permission_classes = (permissions.IsAdminUser,)

    def retrieve(self, request, *args, **kwargs):
        """Return a temporary sequence download link.

        Args:
            request: The current API request.
            args: Additional positional view arguments.
            kwargs: View arguments containing the sequence primary key.
        """
        primary_key = kwargs['pk']
        seqid = SequenceData.objects.get(pk=primary_key).seqid
        sas_url = get_storage_client().create_transfer_url(
            'processed-data',
            'download',
            name=seqid + '.fasta',
            expires_in_seconds=3600,
        )
        return JsonResponse(
            {'id': primary_key, 'seqid': seqid, 'download_link': sas_url}
        )


@login_required
def database_home(request):
    """Render the Sequence Database home page.

    Args:
        request: The current HTTP request.
    """
    return render(request, 'sequence_database/database_home.html')


def generic_autocompleter(non_target_dict):
    """Filter sequences by the forwarded autocomplete fields.

    Args:
        non_target_dict: Field names mapped to their selected values.

    Returns:
        A queryset matching all non-empty forwarded values.
    """
    q = Q()
    qs = SequenceData.objects.all()
    lookup_dict = {
        'genus': 'genus__genus',
        'species': 'species__species',
        'mlst': 'mlst__mlst',
        'rmlst': 'rmlst__rmlst',
        'mlstcc': 'mlst_cc__mlst_cc',
        'serovar': 'serovar__serovar',
        'geneseekr': 'geneseekr__geneseekr',
        'vtyper': 'vtyper__vtyper',
    }
    for field, query in non_target_dict.items():
        if query:
            kwargs = {str(f'{lookup_dict[field]}'): str(query)}
            q = q & Q(**kwargs)
    qs = qs.filter(q)
    return qs


def category_chooser(target):
    """Return all autocomplete fields except the selected target.

    Args:
        target: The field currently being completed.

    Returns:
        The forwarded fields that should constrain this autocomplete.
    """
    categories = [
        'genus',
        'species',
        'mlst',
        'rmlst',
        'mlstcc',
        'serovar',
        'geneseekr',
        'vtyper',
    ]
    return [category for category in categories if category != target]


class GenusAutoCompleter(autocomplete.Select2ListView):
    """Complete genus values using selected metadata filters."""

    def __init__(self, **kwargs):
        """Initialize the autocomplete with genus as its target field.

        Args:
            kwargs: Options passed to the parent list view.
        """
        self.category = 'genus'
        super().__init__(**kwargs)

    def get_list(self):
        """Return matching genus values."""
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target]:
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs = qs.filter(genus__genus__contains=self.q)
        else:
            qs = UniqueGenus.objects.all()
            if self.q:
                qs = qs.filter(genus__icontains=self.q)
        return sorted(list(set(str(result.genus) for result in qs)))


class SpeciesAutoCompleter(autocomplete.Select2ListView):
    """Complete species values using selected metadata filters."""

    def __init__(self, **kwargs):
        """Initialize the autocomplete with species as its target field.

        Args:
            kwargs: Options passed to the parent list view.
        """
        self.category = 'species'
        super().__init__(**kwargs)

    def get_list(self):
        """Return matching species values."""
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target]:
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs = qs.filter(species__species__icontains=self.q)
        else:
            qs = UniqueSpecies.objects.all()
            if self.q:
                qs = qs.filter(species__icontains=self.q)
        return sorted(
            list(
                set(
                    str(result.species)
                    for result in qs
                    if str(result.species) != 'ND'
                )
            )
        )


class MLSTAutoCompleter(autocomplete.Select2ListView):
    """Complete MLST profiles using selected metadata filters."""

    def __init__(self, **kwargs):
        """Initialize the autocomplete with MLST as its target field.

        Args:
            kwargs: Options passed to the parent list view.
        """
        self.category = 'mlst'
        super().__init__(**kwargs)

    def get_list(self):
        """Return matching MLST profiles."""
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target]:
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs = qs.filter(mlst__mlst__icontains=self.q)
        else:
            qs = UniqueMLST.objects.all()
            if self.q:
                qs = qs.filter(mlst__icontains=self.q)
        int_output = sorted(
            list(
                set(
                    int(str(result.mlst))
                    for result in qs
                    if str(result.mlst) != 'ND'
                )
            )
        )
        return [str(integer) for integer in int_output]


class RMLSTAutoCompleter(autocomplete.Select2ListView):
    """Complete rMLST profiles using selected metadata filters."""

    def __init__(self, **kwargs):
        """Initialize the autocomplete with rMLST as its target field.

        Args:
            kwargs: Options passed to the parent list view.
        """
        self.category = 'rmlst'
        super().__init__(**kwargs)

    def get_list(self):
        """Return matching rMLST profiles."""
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target]:
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs = qs.filter(rmlst__rmlst__icontains=self.q)
        else:
            qs = UniqueRMLST.objects.all()
            if self.q:
                qs = qs.filter(rmlst__icontains=self.q)
        int_output = sorted(
            list(
                set(
                    int(str(result.rmlst))
                    for result in qs
                    if str(result.rmlst) != 'ND'
                )
            )
        )
        return [str(integer) for integer in int_output]


class MLSTCCAutoCompleter(autocomplete.Select2ListView):
    """Complete MLST clonal complexes using selected metadata filters."""

    def __init__(self, **kwargs):
        """Initialize the autocomplete with MLST-CC as its target field.

        Args:
            kwargs: Options passed to the parent list view.
        """
        self.category = 'mlstcc'
        super().__init__(**kwargs)

    def get_list(self):
        """Return matching MLST clonal complexes."""
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target]:
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs = qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        else:
            qs = UniqueMLSTCC.objects.all()
            if self.q:
                qs = qs.filter(mlst_cc__icontains=self.q)
        return sorted(list(set(str(result.mlst_cc) for result in qs)))


class SerovarAutoCompleter(autocomplete.Select2ListView):
    """Complete serovars using selected metadata filters."""

    def __init__(self, **kwargs):
        """Initialize the autocomplete with serovar as its target field.

        Args:
            kwargs: Options passed to the parent list view.
        """
        self.category = 'serovar'
        super().__init__(**kwargs)

    def get_list(self):
        """Return matching serovars."""
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target]:
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs = qs.filter(serovar__serovar__icontains=self.q)
        else:
            qs = UniqueSerovar.objects.all()
            if self.q:
                qs = qs.filter(serovar__icontains=self.q)
        return sorted(list(set(str(result.serovar) for result in qs)))


# Create your views here.
@login_required
def database_filter(request):
    """Filter sequence metadata by predefined fields and date bounds.

    Args:
        request: The current HTTP request.
    """
    form = DatabaseRequestForm()
    if request.method == 'POST':
        form = DatabaseRequestForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data.get('start_date')
            end_date = form.cleaned_data.get('end_date')
            q = Q()
            qs = SequenceData.objects.all()
            lookup_dict = {
                'genus': 'genus__genus__iexact',
                'species': 'species__species__iexact',
                'mlst': 'mlst__mlst__iexact',
                'rmlst': 'rmlst__rmlst__iexact',
                'mlstcc': 'mlst_cc__mlst_cc__iexact',
                'geneseekr': 'geneseekr__geneseekr__icontains',
                'serovar': 'serovar__serovar__iexact',
                'vtyper': 'vtyper__vtyper__icontains',
            }
            for field, query in form.cleaned_data.items():
                if str(query) != 'None' and str(query) and 'date' not in field:
                    kwargs = {str(f'{lookup_dict[field]}'): str(query)}
                    q = q & Q(**kwargs)
            qs = qs.filter(q)
            if start_date:
                qs = qs.filter(typing_date__gte=start_date)
            if end_date:
                qs = qs.filter(typing_date__lte=end_date)
            seqid_list = list()
            cfiaid_list = list()
            for sequence_data in qs:
                seqid_list.append(sequence_data.seqid)
                cfiaid_list.append(sequence_data.cfiaid.cfiaid)
            headers = [
                'SeqID',
                'CFIA ID',
                'rMLST Profile',
                'MLST Profile',
                'MLST Clonal Complex',
                'Genus',
                'Species',
                'Serovar',
                'GeneSeekr Profile',
                'Vtyper Profile',
                'Database Version',
                'Typing Date',
            ]
            paginator = Paginator(qs.order_by('seqid'), 100)
            result = paginator.page(1)
            return render(
                request,
                'sequence_database/database_filter_results.html',
                {
                    'query_set': result,
                    'headers': headers,
                    'seqids': seqid_list,
                    'cfiaids': cfiaid_list,
                },
            )
    return render(
        request, 'sequence_database/database_filter.html', {'form': form}
    )


def form_lookup(form):
    """Map a validated query form to its model field and lookup.

    Args:
        form: A validated ``DatabaseFieldForm``.

    Returns:
        The model field path and lookup qualifier.
    """
    lookup_dict = {
        'GENUS': 'genus__genus',
        'SPECIES': 'species__species',
        'MLST': 'mlst__mlst',
        'RMLST': 'rmlst__rmlst',
        'MLSTCC': 'mlst_cc__mlst_cc',
        'GENESEEKR': 'geneseekr__geneseekr',
        'SEROVAR': 'serovar__serovar',
        'VTYPER': 'vtyper__vtyper',
        'VERSION': 'version',
        'CONTAINS': 'icontains',
        'EXACT': 'iexact',
    }
    field = lookup_dict[form.cleaned_data.get('database_fields')]
    qualifier = lookup_dict[form.cleaned_data.get('qualifiers')]
    return field, qualifier


@login_required()
def database_query(request):
    """Render advanced metadata queries built from a formset.

    Args:
        request: The current HTTP request.
    """
    database_date = DatabaseDateForm()
    database_formset_factory = formset_factory(
        form=DatabaseFieldForm, formset=SequenceDatabaseBaseFormSet
    )
    query_set = SequenceData.objects.all()
    table = SequenceDataTable(query_set, order_by='seqid')

    seqid_list = list()
    cfiaid_list = list()
    headers = [
        'SeqID',
        'CFIA ID',
        'rMLST Profile',
        'MLST Profile',
        'MLST Clonal Complex',
        'Genus',
        'Species',
        'Serovar',
        'GeneSeekr Profile',
        'Vtyper Profile',
        'Database Version',
        'Typing Date',
    ]
    if request.method == 'POST':
        database_date = DatabaseDateForm(request.POST)
        database_form_set = database_formset_factory(request.POST)
        if database_date.is_valid() and database_form_set.is_valid():
            start = database_date.cleaned_data.get('start_date')
            end = database_date.cleaned_data.get('end_date')
            if start is None:
                start = datetime.datetime.strptime(
                    '-'.join(['1900', '1', '1']), '%Y-%m-%d'
                )
            if end is None:
                end = datetime.date.today()
                end = end.strftime('%Y-%m-%d')
            query_set = query_set.filter(typing_date__range=[start, end])
            q = Q()
            for form in database_form_set:
                if form.cleaned_data:
                    field, qualifier = form_lookup(form=form)
                    operator = form.cleaned_data.get('query_operators')
                    query = form.cleaned_data.get('query')
                    if query != '' and query is not None:
                        kwargs = {str(f'{field}__{qualifier}'): str(query)}
                        if operator == 'AND':
                            q = q & Q(**kwargs)
                        elif operator == 'OR':
                            q = q | Q(**kwargs)
                        elif operator == 'NOT':
                            q = q & ~Q(**kwargs)
                        else:
                            q = q
            query_set = query_set.filter(q)

        for sequence_data in query_set:
            seqid_list.append(sequence_data.seqid)
            cfiaid_list.append(sequence_data.cfiaid.cfiaid)
        table = SequenceDataTable(query_set, order_by='seqid')
        table.orderable = False
        RequestConfig(
            request, paginate={'paginator_class': LazyPaginator}
        ).configure(table)
        return render(
            request,
            'sequence_database/database_query.html',
            {
                'form_set': database_form_set,
                'date': database_date,
                'database_result': table,
                'headers': headers,
                'count': query_set.count(),
                'seqids': seqid_list,
                'cfiaids': cfiaid_list,
            },
        )

    else:
        RequestConfig(
            request, paginate={'paginator_class': LazyPaginator}
        ).configure(table)
        database_form_set = database_formset_factory()
        return render(
            request,
            'sequence_database/database_query.html',
            {
                'form_set': database_form_set,
                'date': database_date,
                'database_result': table,
                'headers': headers,
                'count': query_set.count(),
                'seqids': seqid_list,
                'cfiaids': cfiaid_list,
            },
        )


@login_required
def id_search(request):
    """Find records by sequence identifier, CFIA ID, or renamed ID.

    Args:
        request: The current HTTP request.
    """
    id_form = DatabaseIDsForm()
    if request.method == 'POST':
        # Populate the form with the POST data
        id_form = DatabaseIDsForm(request.POST)
        if id_form.is_valid():
            # Extract the SEQIDs and/or CFIA IDs in the form
            ids = id_form.cleaned_data
            # Initialise a query set of all objects in the SequenceData model
            query_set = SequenceData.objects.all()
            seqids = list()
            # Initialise a Q object for building the query
            q = Q()
            # Initialise list to store missing and renamed IDs
            missing_ids = list()
            renamed_ids = list()
            # Only proceed if the user has supplied a list of IDs
            if ids:
                for query_id in ids:
                    # SEQIDs start with a digit, while CFIA IDs do not
                    if query_id[0].isdigit():
                        # Filter the query set with the SEQID
                        results = query_set.filter(seqid__exact=query_id)
                        # Search aliases when no sequence record exists.
                        if not results:
                            # Initialise a query set of LookupTable objects
                            lookup_table = LookupTable.objects.all()
                            match = False
                            for entry in lookup_table:
                                if query_id in entry.other_names:
                                    renamed_ids.append(
                                        (query_id, str(entry.cfiaid))
                                    )
                                    query_id = entry.cfiaid
                                    match = True
                            if not match:
                                missing_ids.append(query_id)
                            else:
                                q = q | Q(cfiaid__cfiaid__exact=query_id)
                        # Update the Q object with the query
                        q = q | Q(seqid__exact=query_id)
                    # Same general idea for CFIA IDs
                    else:
                        results = query_set.filter(
                            cfiaid__cfiaid__exact=query_id
                        )
                        if not results:
                            missing_ids.append(query_id)
                        q = q | Q(cfiaid__cfiaid__exact=query_id)
                # Filter using the supplied IDs. An empty Q matches every row.
                query_set = query_set.filter(q)
            else:
                query_set = query_set.none()
            # Add the SEQIDs to the list
            for entry in query_set:
                seqids.append(entry.seqid)
            return render(
                request,
                'sequence_database/database_id_search.html',
                {
                    'form': id_form,
                    'query_table': query_set,
                    'seqids': seqids,
                    'missing_ids': missing_ids,
                    'renamed': renamed_ids,
                },
            )
    return render(
        request,
        'sequence_database/database_id_search.html',
        {'form': id_form, 'query_table': DatabaseRequest.objects.all()},
    )
