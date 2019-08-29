from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.conf import settings
from django.db.models import Q
from olc_webportalv2.metadata.forms import MetaDataRequestForm, make_species_choice_list, make_genus_choice_list, \
    make_mlst_choice_list, make_rmlst_choice_list, make_serotype_choice_list
from olc_webportalv2.metadata.models import MetaDataRequest, SequenceData, LabID, OLNID
from olc_webportalv2.metadata.serializers import SequenceDataSerializer, OLNSerializer
import datetime
from django.contrib.auth.decorators import login_required
from dal import autocomplete
from rest_framework import generics, permissions, pagination, views, parsers, response
from azure.storage.blob import BlockBlobService, BlobPermissions


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
        sas_token = blob_service.generate_blob_shared_access_signature(container_name='processed-data',
                                                                       blob_name=seqid + '.fasta',
                                                                       permission=BlobPermissions.READ,
                                                                       expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=1))
        sas_url = blob_service.make_blob_url(container_name='processed-data',
                                             blob_name=seqid + '.fasta',
                                             sas_token=sas_token)
        return JsonResponse({'id': primary_key,
                             'seqid': seqid,
                             'download_link': sas_url})


class GenusAutoCompleter(autocomplete.Select2ListView):
    def get_list(self):
        return make_genus_choice_list()


class SpeciesAutoCompleter(autocomplete.Select2ListView):
    def get_list(self):
        return make_species_choice_list()


class SerotypeAutoCompleter(autocomplete.Select2ListView):
    def get_list(self):
        return make_serotype_choice_list()


class MLSTAutoCompleter(autocomplete.Select2ListView):
    def get_list(self):
        return make_mlst_choice_list()


class RMLSTAutoCompleter(autocomplete.Select2ListView):
    def get_list(self):
        return make_rmlst_choice_list()


# Create your views here.
@login_required
def metadata_home(request):
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
            criteria_dict = dict()
            # Filter based on each possible criterion, if user put anything.
            if species != '':
                sequence_data_matching_query = sequence_data_matching_query.filter(species__iexact=species)
                criteria_dict['species'] = species
            if serotype != '':
                sequence_data_matching_query = sequence_data_matching_query.filter(serotype__iexact=serotype)
                criteria_dict['serotype'] = serotype
            if mlst != '':
                sequence_data_matching_query = sequence_data_matching_query.filter(mlst__iexact=mlst)
                criteria_dict['mlst'] = mlst
            if rmlst != '':
                sequence_data_matching_query = sequence_data_matching_query.filter(rmlst__iexact=rmlst)
                criteria_dict['rmlst'] = rmlst
            if genus != '':
                sequence_data_matching_query = sequence_data_matching_query.filter(genus__iexact=genus)
                criteria_dict['genus'] = genus
            
            # Deal with quality.
            if quality == 'Pass':
                sequence_data_matching_query = sequence_data_matching_query.exclude(quality='Fail')
                criteria_dict['quality'] = 'Pass + Reference'
            elif quality == 'Reference':
                sequence_data_matching_query = sequence_data_matching_query.filter(quality='Reference')
                criteria_dict['quality'] = 'Reference'
            else:
                criteria_dict['quality'] = 'All'
                
            seqid_list = list()
            for sequence_data in sequence_data_matching_query:
                seqid_list.append(sequence_data.seqid)

            metadata_request = MetaDataRequest.objects.create(seqids=seqid_list,
                                                              criteria=criteria_dict)
            metadata_request.save()
            return redirect('metadata:metadata_results', metadata_request_pk=metadata_request.pk)
    return render(request,
                  'metadata/metadata_home.html',
                  {
                     'form': form
                  })


@login_required
def metadata_results(request, metadata_request_pk):
    metadata_result = get_object_or_404(MetaDataRequest, pk=metadata_request_pk)
    idDict = id_sync(metadata_result)
    return render(request,
                  'metadata/metadata_results.html',
                  {
                      'metadata_result': metadata_result, 'idDict': idDict
                  })


@login_required
def metadata_browse(request):
    sequence_data = SequenceData.objects.filter().select_related('labid')
    return render(request,
                  'metadata/metadata_browse.html',
                  {
                      'sequence_data': sequence_data
                  })

def id_sync(metadata_result):
    idDict = dict()
    data_set = SequenceData.objects.filter(seqid__in=metadata_result.seqids)
    for item in data_set:
        if item.labid is not None:
            labid_result = str(item.labid)
        else:
            labid_result = 'N/A'

        if item.olnid is not None:
            olnid_result = str(item.olnid)
        else:
            olnid_result = 'N/A'  

        idDict.update({item.seqid:(labid_result,olnid_result)})
    return idDict,
    