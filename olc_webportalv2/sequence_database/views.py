from django.shortcuts import render
from django.http import JsonResponse
from django.conf import settings
from olc_webportalv2.sequence_database.forms import DatabaseRequestForm, DatabaseIDsForm, DatabaseFieldForm, \
    SequenceDatabaseBaseFormSet, DatabaseDateForm
from olc_webportalv2.sequence_database.models import DatabaseRequest, SequenceData, OLNID, LookupTable, UniqueGenus, \
    UniqueSpecies, UniqueMLST, UniqueMLSTCC, UniqueRMLST
from olc_webportalv2.sequence_database.serializers import SequenceDataSerializer, OLNSerializer
import datetime
from django.contrib.auth.decorators import login_required
from dal import autocomplete
from rest_framework import generics, permissions, pagination
from azure.storage.blob import BlockBlobService, BlobPermissions
# from django.utils.translation import ugettext_lazy as _
from django.db.models import Q
from django.forms.formsets import formset_factory
from olc_webportalv2.sequence_database.tables import SequenceDataTable


# Not sure where to put this - create pagination.py?
class LargeResultsPagination(pagination.PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 20000


class OLNList(generics.ListCreateAPIView):
    queryset = OLNID.objects.all()
    serializer_class = OLNSerializer
    pagination_class = LargeResultsPagination


class OLNDetail(generics.RetrieveAPIView):
    serializer_class = OLNSerializer
    permission_classes = (permissions.AllowAny,)

    def get_queryset(self):
        oln_id = self.kwargs['oln_id']
        oln = OLNID.objects.get(olnid=oln_id)
        return oln

    def retrieve(self, request, *args, **kwargs):
        oln = self.get_queryset()
        seqids = SequenceData.objects.filter(olnid=oln)
        seqdata_dict = dict()
        for seqid in seqids:
            seqdata_dict[seqid.seqid] = seqid.quality
        return JsonResponse(seqdata_dict)

    def handle_exception(self, exc):
        return JsonResponse({'ERROR': str(exc)})


class SequenceDataList(generics.ListCreateAPIView):
    queryset = SequenceData.objects.all()
    serializer_class = SequenceDataSerializer
    permission_classes = (permissions.IsAdminUser,)
    pagination_class = LargeResultsPagination


class SequenceDataDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = SequenceData.objects.all()
    serializer_class = SequenceDataSerializer
    permission_classes = (permissions.IsAdminUser,)

    def retrieve(self, request, *args, **kwargs):
        primary_key = kwargs['pk']
        seqid = SequenceData.objects.get(pk=primary_key).seqid
        blob_service = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                        account_name=settings.AZURE_ACCOUNT_NAME)
        sas_token = blob_service \
            .generate_blob_shared_access_signature(container_name='processed-data',
                                                   blob_name=seqid + '.fasta',
                                                   permission=BlobPermissions.READ,
                                                   expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=1))
        sas_url = blob_service.make_blob_url(container_name='processed-data',
                                             blob_name=seqid + '.fasta',
                                             sas_token=sas_token)
        return JsonResponse({'id': primary_key,
                             'seqid': seqid,
                             'download_link': sas_url})


@login_required
def database_home(request):
    return render(request, 'sequence_database/database_home.html')


def generic_autocompleter(non_target_dict):
    """
    Use Q objects to create an autocomplete filter based on the input dictionary
    :param: non_target_dict: Dictionary of field name: query e.g. rmlst: 2124
    :return Filtered query set
    """
    #  Initialise a Q object for building the queries
    q = Q()
    #  The query set will begin as all entries in the SequenceData model
    qs = SequenceData.objects.all()
    # Dictionary linking the field name to the appropriate table in the SequenceData model
    lookup_dict = {
        'genus': 'genus__genus',
        'species': 'species__species',
        'mlst': 'mlst__mlst',
        'rmlst': 'rmlst__rmlst',
        'mlstcc': 'mlst_cc__mlst_cc',
    }
    # Iterate through all the fields in the dictionary
    for field, query in non_target_dict.items():
        # Only update the Q object if the query string exists
        if query:
            # Set the kwargs as the table lookup: query e.g. rmlst__rmlst: 2124
            kwargs = {
                str('{db_table}'.format(db_table=lookup_dict[field])): str(query)
            }
            # Update the Q object with the kwargs
            q = q & Q(**kwargs)
    # Filter the query set with the Q object
    qs = qs.filter(q)
    return qs


def category_chooser(target):
    """
    Create a list of non-targets from a list of all possible non-targets by eliminating the single provided target
    :param target: Current category being investigated e.g. genus
    :return: List of non-targets
    """
    # Initialise the list
    non_targets = list()
    # Iterate through all possible target categories
    for category in ['genus', 'species', 'mlst', 'rmlst', 'mlstcc']:
        # Add the current category to the list if it does not match the name of the target
        if category != target:
            non_targets.append(category)
    return non_targets


class GenusAutoCompleter(autocomplete.Select2ListView):

    def __init__(self, **kwargs):
        self.category = 'genus'
        super().__init__(**kwargs)

    def get_list(self):
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target] != '':
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        else:
            qs = UniqueGenus.objects.all()
            if self.q:
                qs.filter(genus__icontains=self.q)
        return sorted(list(set(str(result.genus) for result in qs)))


class SpeciesAutoCompleter(autocomplete.Select2ListView):

    def __init__(self, **kwargs):
        self.category = 'species'
        super().__init__(**kwargs)

    def get_list(self):
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target] != '':
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        else:
            qs = UniqueSpecies.objects.all()
            if self.q:
                qs.filter(species__icontains=self.q)
        return sorted(list(set(str(result.species) for result in qs)))


class MLSTAutoCompleter(autocomplete.Select2ListView):

    def __init__(self, **kwargs):
        self.category = 'mlst'
        super().__init__(**kwargs)

    def get_list(self):
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target] != '':
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        else:
            qs = UniqueMLST.objects.all()
            if self.q:
                qs.filter(mlst__icontains=self.q)
        int_output = sorted(list(set(int(str(result.mlst)) for result in qs if str(result.mlst) != 'ND')))
        return [str(integer) for integer in int_output]


class RMLSTAutoCompleter(autocomplete.Select2ListView):

    def __init__(self, **kwargs):
        self.category = 'rmlst'
        super().__init__(**kwargs)

    def get_list(self):
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target] != '':
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        else:
            qs = UniqueRMLST.objects.all()
            if self.q:
                qs.filter(rmlst__icontains=self.q)
        int_output = sorted(list(set(int(str(result.rmlst)) for result in qs if str(result.rmlst) != 'ND')))
        return [str(integer) for integer in int_output]


class MLSTCCAutoCompleter(autocomplete.Select2ListView):

    def __init__(self, **kwargs):
        self.category = 'mlstcc'
        super().__init__(**kwargs)

    def get_list(self):
        non_targets = category_chooser(target=self.category)
        non_target_dict = dict()
        query = False
        for non_target in non_targets:
            non_target_dict[non_target] = self.forwarded.get(non_target, None)
            if non_target_dict[non_target] != '':
                query = True
        if query:
            qs = generic_autocompleter(non_target_dict=non_target_dict)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        else:
            qs = UniqueMLSTCC.objects.all()
            if self.q:
                qs.filter(mlst_cc__icontains=self.q)
        return sorted(list(set(str(result.mlst_cc) for result in qs)))


# Create your views here.
@login_required
def database_filter(request):
    form = DatabaseRequestForm()
    if request.method == 'POST':
        form = DatabaseRequestForm(request.POST)
        if form.is_valid():
            # As the date filtering field is more complex, extract only these values from the cleaned_data
            start_date = form.cleaned_data.get('start_date')
            end_date = form.cleaned_data.get('end_date')
            # Initialise a Q object to build queries
            q = Q()
            # Initalise a query set from the SequenceData model
            qs = SequenceData.objects.all()
            # Dictionary linking the field name to the appropriate table in the SequenceData model
            lookup_dict = {
                'genus': 'genus__genus__iexact',
                'species': 'species__species__iexact',
                'mlst': 'mlst__mlst__iexact',
                'rmlst': 'rmlst__rmlst__iexact',
                'mlstcc': 'mlst_cc__mlst_cc__iexact',
                'geneseekr': 'geneseekr__geneseekr__icontains',
                'serovar': 'serovar__serovar__icontains',
                'vtyper': 'vtyper__vtyper__icontains'
            }
            # Iterate through all the cleaned data
            for field, query in form.cleaned_data.items():
                # Ensure that the query exists, and is not None, and that 'date' is not in the field, as these are
                # being processed separately
                if str(query) != 'None' and str(query) and 'date' not in field:
                    # Create the kwargs based on the category, the appropriate table and the query,
                    # e.g. genus, Escherichia will create a dictionary entry of genus__genus__iexact: Escherichia
                    kwargs = {
                        str('{db_table}'.format(db_table=lookup_dict[field])): str(query)
                    }
                    # Update the Q object with the kwargs
                    q = q & Q(**kwargs)
            # Filter the query set with the Q object
            qs = qs.filter(q)
            # Filter based on date range separately from the other categories
            if start_date and end_date:
                qs = qs.filter(typing_date__range=[start_date, end_date])
            # Initialise list to store the appropriate IDs
            seqid_list = list()
            cfiaid_list = list()
            # Populate the lists with the necessary IDs
            for sequence_data in qs:
                seqid_list.append(sequence_data.seqid)
                cfiaid_list.append(sequence_data.cfiaid.cfiaid)
            return render(request,
                          'sequence_database/database_filter_results.html',
                          {
                              'table': qs,
                              'seqids': seqid_list,
                              'cfiaids': cfiaid_list
                          })
    return render(request,
                  'sequence_database/database_filter.html',
                  {
                      'form': form
                  })


def form_lookup(form):
    """
    Extracts necessary values from a populated form, and looks up the necessary database table with the extracted values
    :param form: Populated DatabaseFieldForm
    :return: Extracted field table e.g. genus__genus, and qualifier e.g. icontains
    """
    # Dictionary linking categories to tables
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
        'EXACT': 'iexact'
    }
    # Extract the table name from the lookup dictionary using the value for database_field from the form
    field = lookup_dict[form.cleaned_data.get('database_fields')]
    # Extract the qualifier from the lookup dictionary using the value for qualifiers from the form
    qualifier = lookup_dict[form.cleaned_data.get('qualifiers')]
    return field, qualifier


@login_required()
def database_query(request):
    database_date = DatabaseDateForm()
    database_formset_factory = formset_factory(form=DatabaseFieldForm, formset=SequenceDatabaseBaseFormSet)
    # Initialise the query set as all objects from the SequenceData model
    # query_set = DataTable(SequenceData.objects.all())
    query_set = SequenceData.objects.all()
    if request.method == 'POST':
        query_set = SequenceData.objects.all()
        # Populate the necessary forms with the POST data
        database_date = DatabaseDateForm(request.POST)
        database_form_set = database_formset_factory(request.POST)
        if database_date.is_valid() and database_form_set.is_valid():
            # Process the dates first - extract the necessary values from the form's cleaned data
            start = database_date.cleaned_data.get('start_date')
            end = database_date.cleaned_data.get('end_date')
            # Update the values as required
            if start is None:
                # If there is no start date provided, set the date to be 1900-01-01
                start = datetime.datetime.strptime("-".join(['1900', '1', '1']), '%Y-%m-%d')
            if end is None:
                # If there is no end date provided, set the date to be today's date
                end = datetime.date.today()
                end = end.strftime('%Y-%m-%d')
            # Filter the query set on the date range
            query_set = query_set.filter(typing_date__range=[start, end])
            # Initialise a Q object for build the queries
            q = Q()
            # Iterate through all the DatabaseFieldForms in the form set
            for form in database_form_set:
                # Ensure that the form contains entries
                if form.cleaned_data:
                    # Extract the table name e.g. genus__genus and the qualifier e.g. icontains from the form data
                    field, qualifier = form_lookup(form=form)
                    # Extract the operate e.g. AND, and the query e.g. Escherichia
                    operator = form.cleaned_data.get('query_operators')
                    query = form.cleaned_data.get('query')
                    # Ensure that a query exists before updating the Q object
                    if query != '' and query is not None:
                        # Create a kwargs dictionary entry of the table name__qualifier: query
                        # e.g. genus__genus__icontains: Escherichia
                        kwargs = {
                            str('{db_table}__{qualifier}'.format(db_table=field,
                                                                 qualifier=qualifier)): str(query)
                        }
                        # Update the Q object appropriately depending on the operator
                        if operator == 'AND':
                            q = q & Q(**kwargs)
                        elif operator == 'OR':
                            q = q | Q(**kwargs)
                        elif operator == 'NOT':
                            q = q & ~Q(**kwargs)
                        else:
                            q = q
            # Filter the query set with the query stored in the Q object
            query_set = query_set.filter(q)

    else:
        database_form_set = database_formset_factory()
    return render(request,
                  'sequence_database/database_query.html',
                  {
                      'form_set': database_form_set,
                      'date': database_date,
                      'database_result': query_set
                  })


@login_required
def id_search(request):
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
                        # If the SEQID is not present in the SequenceData table, look for it in the LookupTable
                        if not results:
                            # Initialise a query set of LookupTable objects
                            lookup_table = LookupTable.objects.all()
                            # Initialise a boolean of whether the SEQID is found in the LookupTable
                            match = False
                            # Iterate through the entries in the LookupTable
                            for entry in lookup_table:
                                # Determine if the SEQID is in the other_names field of the current entry
                                if query_id in entry.other_names:
                                    # Update the renamed_ids list with a tuple of SEQID, CFIA ID of entry
                                    renamed_ids.append((query_id, str(entry.cfiaid)))
                                    # Update the current query ID to be the CFIA ID of the entry
                                    query_id = entry.cfiaid
                                    # Store the fact that a match was found
                                    match = True
                            # If the SEQID was not in the LookupTable, add it to the list of missing SEQIDs
                            if not match:
                                missing_ids.append(query_id)
                            else:
                                q = q | Q(cfiaid__cfiaid__exact=query_id)
                        # Update the Q object with the query
                        q = q | Q(seqid__exact=query_id)
                    # Same general idea for CFIA IDs
                    else:
                        # Check to see if there are any CFIA IDs matching the supplied query ID
                        results = query_set.filter(cfiaid__cfiaid__exact=query_id)
                        # If there are no matches, update the list of missing IDs with the query ID
                        if not results:
                            missing_ids.append(query_id)
                        # Update the query
                        q = q | Q(cfiaid__cfiaid__exact=query_id)
            # Filter the query set with the query build above
            query_set = query_set.filter(q)
            # Add the SEQIDs to the list
            for entry in query_set:
                seqids.append(entry.seqid)
            return render(request,
                          'sequence_database/database_id_search.html',
                          {
                              'form': id_form,
                              'query_table': query_set,
                              'seqids': seqids,
                              'missing_ids': missing_ids,
                              'renamed': renamed_ids
                          }
                          )
    return render(request,
                  'sequence_database/database_id_search.html',
                  {
                      'form': id_form,
                      'query_table': DatabaseRequest.objects.all()
                  }
                  )
