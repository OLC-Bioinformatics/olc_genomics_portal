from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.conf import settings
from olc_webportalv2.sequence_database.forms import DatabaseRequestForm
from olc_webportalv2.sequence_database.models import DatabaseRequest, DatabaseRequestIDs, SequenceData, OLNID
from olc_webportalv2.sequence_database.serializers import SequenceDataSerializer, OLNSerializer
from olc_webportalv2.sequence_database.models import UniqueGenus, UniqueSpecies, UniqueMLST, UniqueMLSTCC, UniqueRMLST, UniqueGeneSeekr, UniqueSerovar, UniqueVtyper
import datetime
from django.contrib.auth.decorators import login_required
from dal import autocomplete
from rest_framework import generics, permissions, pagination
from azure.storage.blob import BlockBlobService, BlobPermissions
from django.utils.translation import ugettext_lazy as _


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

class GenusAutoCompleter(autocomplete.Select2ListView):

    def get_list(self):
        species = self.forwarded.get('species', None)
        mlst = self.forwarded.get('mlst', None)
        rmlst = self.forwarded.get('rmlst', None)
        mlstcc = self.forwarded.get('mlstcc', None)
        # ('species', 'mlst', 'rmlst', 'mlstcc')
        if species and mlst and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('species', 'mlst', 'rmlst')
        elif species and mlst and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('species', 'mlst', 'mlstcc')
        elif species and mlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst__mlst__exact=mlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('species', 'rmlst', 'mlstcc')
        elif species and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('mlst', 'rmlst', 'mlstcc')
        elif mlst and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        #  ('species', 'mlst')
        elif species and mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('species', 'rmlst')
        elif species and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('species', 'mlstcc')
        elif species and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('mlst', 'rmlst')
        elif mlst and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('mlst', 'mlstcc')
        elif mlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('rmlst', 'mlstcc')
        elif rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('species')
        elif species:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('mlst')
        elif mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('rmlst')
        elif rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        # ('mlstcc')
        elif mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(genus__genus__contains=self.q)
        else:
            qs = UniqueGenus.objects.all()
            if self.q:
                qs.filter(genus__contains=self.q)

        return sorted(list(set(str(result.genus) for result in qs)))


class SpeciesAutoCompleter(autocomplete.Select2ListView):
    def get_list(self):
        genus = self.forwarded.get('genus', None)
        mlst = self.forwarded.get('mlst', None)
        rmlst = self.forwarded.get('rmlst', None)
        mlstcc = self.forwarded.get('mlstcc', None)
        # ('genus', 'mlst', 'rmlst', 'mlstcc')
        if genus and mlst and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('genus', 'mlst', 'rmlst')
        elif genus and mlst and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('genus', 'mlst', 'mlstcc')
        elif genus and mlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst__mlst__exact=mlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('genus', 'rmlst', 'mlstcc')
        elif genus and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('mlst', 'rmlst', 'mlstcc')
        elif mlst and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('genus', 'mlst')
        elif genus and mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('genus', 'rmlst')
        elif genus and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('genus', 'mlstcc')
        elif genus and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('mlst', 'rmlst')
        elif mlst and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('mlst', 'mlstcc')
        elif mlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('rmlst', 'mlstcc')
        elif rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('genus',)
        elif genus:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('mlst',)
        elif mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('rmlst',)
        elif rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        # ('mlstcc',)
        elif mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(species__species__icontains=self.q)
        else:
            qs = UniqueSpecies.objects.all()
            if self.q:
                qs.filter(species__icontains=self.q)

        return sorted(list(set(str(result.species) for result in qs)))


class MLSTAutoCompleter(autocomplete.Select2ListView):

    def get_list(self):
        genus = self.forwarded.get('genus', None)
        species = self.forwarded.get('species', None)
        rmlst = self.forwarded.get('rmlst', None)
        mlstcc = self.forwarded.get('mlstcc', None)
        # ('genus', 'species', 'rmlst', 'mlstcc')
        if genus and species and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('genus', 'species', 'rmlst')
        elif genus and species and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('genus', 'species', 'mlstcc')
        elif genus and species and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('genus', 'rmlst', 'mlstcc')
        elif genus and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('species', 'rmlst', 'mlstcc')
        elif species and rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('genus', 'species')
        elif genus and species:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('genus', 'rmlst')
        elif genus and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('genus', 'mlstcc')
        elif genus and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('species', 'rmlst')
        elif species and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('species', 'mlstcc')
        elif species and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('rmlst', 'mlstcc')
        elif rmlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(rmlst__rmlst__exact=rmlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('genus',)
        elif genus:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('species',)
        elif species:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('rmlst',)
        elif rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        # ('mlstcc',)
        elif mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(mlst__mlst__icontains=self.q)
        else:
            qs = UniqueMLST.objects.all()
            if self.q:
                qs.filter(mlst__icontains=self.q)
        int_output = sorted(list(set(int(str(result.mlst)) for result in qs if str(result.mlst) != 'ND')))
        return [str(integer) for integer in int_output]


class RMLSTAutoCompleter(autocomplete.Select2ListView):

    def get_list(self):
        # GenericAutoCompleter(permutations=['genus', 'species', 'mlst', 'mlstcc'])
        genus = self.forwarded.get('genus', None)
        species = self.forwarded.get('species', None)
        mlst = self.forwarded.get('mlst', None)
        mlstcc = self.forwarded.get('mlstcc', None)
        # ('genus', 'species', 'mlst', 'mlstcc')
        if genus and species and mlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           mlst__mlst__exact=mlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('genus', 'species', 'mlst')
        elif genus and species and mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('genus', 'species', 'mlstcc')
        elif genus and species and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('genus', 'mlst', 'mlstcc')
        elif genus and mlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst__mlst__exact=mlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('species', 'mlst', 'mlstcc')
        elif species and mlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst__mlst__exact=mlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('genus', 'species')
        elif genus and species:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('genus', 'mlst')
        elif genus and mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('genus', 'mlstcc')
        elif genus and mlstcc:
            print('genus and mlstcc', genus, mlstcc)
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst_cc__mlst_cc__exact=mlstcc)
            print(len(qs))
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('species', 'mlst')
        elif species and mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('species', 'mlstcc')
        elif species and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('mlst', 'mlstcc')
        elif mlst and mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst,
                           mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('genus',)
        elif genus:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('species',)
        elif species:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('mlst',)
        elif mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        # ('mlstcc',)
        elif mlstcc:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst_cc__mlst_cc__exact=mlstcc)
            if self.q:
                qs.filter(rmlst__rmlst__icontains=self.q)
        else:
            qs = UniqueRMLST.objects.all()
            if self.q:
                qs.filter(rmlst__icontains=self.q)
        int_output = sorted(list(set(int(str(result.rmlst)) for result in qs if str(result.rmlst) != 'ND')))
        return [str(integer) for integer in int_output]


class MLSTCCAutoCompleter(autocomplete.Select2ListView):
    def get_list(self):
        genus = self.forwarded.get('genus', None)
        species = self.forwarded.get('species', None)
        mlst = self.forwarded.get('mlst', None)
        rmlst = self.forwarded.get('rmlst', None)

        if genus and species and mlst and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif genus and species and mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif genus and species and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif species and mlst and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif genus and species:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           species__species__iexact=species)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif genus and mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif genus and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif species and mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif species and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif mlst and rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst,
                           rmlst__rmlst__exact=rmlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif genus:
            qs = SequenceData.objects.all()
            qs = qs.filter(genus__genus__iexact=genus)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif species:
            qs = SequenceData.objects.all()
            qs = qs.filter(species__species__iexact=species)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif mlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(mlst__mlst__exact=mlst)
            if self.q:
                qs.filter(mlst_cc__mlst_cc__icontains=self.q)
        elif rmlst:
            qs = SequenceData.objects.all()
            qs = qs.filter(rmlst__rmlst__exact=rmlst)
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
        print('POST!')
        form = DatabaseRequestForm(request.POST)
        # print('FORM!')
        #
        print('post genus', request.POST['genus'])
        # sequence_data_matching_query = SequenceData.objects.all()
        # genus = sequence_data_matching_query.filter(genus__genus__contains=request.POST['genus'])
        # criteria_dict = dict()
        # if genus:
        #     criteria_dict['genus'] = genus
        # seqid_list = list()
        # for sequence_data in genus:
        #     seqid_list.append(str(sequence_data))
        # print(seqid_list)
        # database_request = DatabaseRequest.objects.create(seqids=seqid_list,
        #                                                   criteria=dict())
        # database_request.save()
        # return redirect('sequence_database:database_results', database_request_pk=database_request.pk)
        if form.is_valid():
            print('VALID')
            genus = str(form.cleaned_data.get('genus')) if form.cleaned_data.get('genus') else None
            print('genus', genus, str(genus))
            species = str(form.cleaned_data.get('species')) if form.cleaned_data.get('species') else None
            print('species', species)
            mlst = str(form.cleaned_data.get('mlst')) if form.cleaned_data.get('mlst') else None
            print(type(mlst))
            print('mlst', mlst)
            mlstcc = str(form.cleaned_data.get('mlstcc')) if form.cleaned_data.get('mlstcc') else None
            rmlst = str(form.cleaned_data.get('rmlst'))  if form.cleaned_data.get('rmlst') else None
            print('rmlst', rmlst)
            serovar = form.cleaned_data.get('serovar')
            geneseekr = form.cleaned_data.get('geneseekr')
            vtyper = form.cleaned_data.get('vtyper')
            # seqids = form.cleaned_data.get('seqids')
            # cfiaids = form.cleaned_data.get('cfiaids')
            start_date = form.cleaned_data.get('start_date')
            end_date = form.cleaned_data.get('end_date')
            # exclude = form.cleaned_data.get('everything_but')
            sequence_data_matching_query = SequenceData.objects.all()
            print('original', len(sequence_data_matching_query))
            # criteria_dict = dict()
            # Filter based on each possible criterion, if user put anything.
            if genus is not None:
                sequence_data_matching_query = sequence_data_matching_query.filter(genus__genus__icontains=genus)
                print('genus', len(sequence_data_matching_query))
                # criteria_dict['genus'] = genus
            if species is not None:
                sequence_data_matching_query = sequence_data_matching_query.filter(species__species__icontains=species)
                print('species', len(sequence_data_matching_query))
                # criteria_dict['species'] = species
            if mlst is not None:
                sequence_data_matching_query = sequence_data_matching_query.filter(mlst__mlst__iexact=mlst)
                print('mlst', len(sequence_data_matching_query))
                # criteria_dict['mlst'] = mlst
            if mlstcc is not None:
                sequence_data_matching_query = sequence_data_matching_query.filter(mlst_cc__mlst_cc__iexact=mlstcc)
                print('mlstcc', len(sequence_data_matching_query))
                # criteria_dict['mlst_cc'] = mlstcc
            if rmlst is not None:
                sequence_data_matching_query = sequence_data_matching_query.filter(rmlst__rmlst__iexact=rmlst)
                print('rmlst', len(sequence_data_matching_query))
                # criteria_dict['rmlst'] = rmlst
            if geneseekr:
                sequence_data_matching_query = sequence_data_matching_query.filter(geneseekr__geneseekr__icontains=geneseekr)
                print('geneseekr', len(sequence_data_matching_query))
                # criteria_dict['geneseekr'] = geneseekr
            if vtyper:
                sequence_data_matching_query = sequence_data_matching_query.filter(vtyper__vtyper__icontains=vtyper)
                print('vtyper', len(sequence_data_matching_query))
                # criteria_dict['vtyper'] = vtyper
            if serovar:
                sequence_data_matching_query = sequence_data_matching_query.filter(serovar__serovar__icontains=serovar)
                print('serovar', len(sequence_data_matching_query))
                # criteria_dict['serovar'] = serovar
            if start_date and end_date:
                sequence_data_matching_query = sequence_data_matching_query.filter(typing_date__range=[start_date, end_date])
                print('date ranged', len(sequence_data_matching_query))
                # criteria_dict['serovar'] = serovar
            seqid_list = list()
            sample_data = list()
            for sequence_data in sequence_data_matching_query:
                seqid_list.append(sequence_data.seqid)
                # for key, value in vars(sequence_data).items():
                #     print(key, value)
                print('data!!!!', sequence_data.seqid, sequence_data.cfiaid.cfiaid)
                # database_request = DatabaseRequest(seqid=sequence_data.seqid,
                #                                                   cfiaid=sequence_data.cfiaid.cfiaid,
                #                                                   genus=sequence_data.genus.genus,
                #                                                   species=sequence_data.species.species,
                #                                                   mlst=sequence_data.mlst.mlst,
                #                                                   mlst_cc=sequence_data.mlst_cc.mlst_cc,
                #                                                   rmlst=sequence_data.rmlst.rmlst,
                #                                                   geneseekr=sequence_data.geneseekr.geneseekr,
                #                                                   serovar=sequence_data.serovar.serovar,
                #                                                   vtyper=sequence_data.vtyper.vtyper,
                #                                                   version=sequence_data.version,
                #                                                   typing_date=sequence_data.typing_date)
                # database_request.save()
            print(len(seqid_list), seqid_list)
            database_request = DatabaseRequestIDs(seqids=seqid_list)
            database_request.save()
            # return redirect('sequence_database:database_results', database_request_pk=database_request.pk)
            # return redirect('sequence_database:database_filter_results')
            return render(request,
                          'sequence_database/database_filter_results.html',
                          {
                              'table': sequence_data_matching_query,
                              'seqids': seqid_list
                          })
        else:
            print('ERRORS', form.errors.as_data())
    return render(request,
                  'sequence_database/database_filter.html',
                  {
                      'form': form
                  })

@login_required
def database_results(request, database_request_pk):
    database_result = get_object_or_404(DatabaseRequestIDs, pk=database_request_pk)
    id_list = database_result.seqids
    # id_list = (str(list(id_dict.keys()))).replace("'", "").replace("[", "").replace("]", "").replace(",", " ")
    return render(request,
                  'sequence_database/database_results.html',
                  {
                      'database_result': database_result, 'idList': id_list
                  })

from django.db.models import Q
from django.forms.formsets import formset_factory
from olc_webportalv2.sequence_database.filters import SequenceDatabaseFilter
from olc_webportalv2.sequence_database.forms import DatabaseFieldForm, SequenceDatabaseBaseFormSet, DatabaseDateForm

def form_lookup(form):
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
    field = lookup_dict[form.cleaned_data.get('database_fields')]
    qualifier = lookup_dict[form.cleaned_data.get('qualifiers')]

    return field, qualifier

@login_required()
def database_browse(request):
    database_date = DatabaseDateForm()
    database_formset_factory = formset_factory(form=DatabaseFieldForm, formset=SequenceDatabaseBaseFormSet)
    query_set = SequenceData.objects.all()
    if request.method == 'POST':
        query_set = SequenceData.objects.all()
        database_date = DatabaseDateForm(request.POST)
        database_form_set = database_formset_factory(request.POST)
        if database_date.is_valid() and database_form_set.is_valid():
            start = database_date.cleaned_data.get('start_date')
            end = database_date.cleaned_data.get('end_date')
            print('start', start, 'end', end)
            if start is None:
                start = datetime.datetime.strptime("-".join(['1900', '1', '1']), '%Y-%m-%d')
            if end is None:
                end = datetime.date.today()
                end = end.strftime('%Y-%m-%d')
            print('before date', len(query_set))
            query_set = query_set.filter(typing_date__range=[start, end])
            print('after date', len(query_set))
            print('VALID!')
            # Uses logic from: https://djangosnippets.org/snippets/1700/
            q = Q()
            for form in database_form_set:
                if form.cleaned_data:
                    print(form.cleaned_data)
                    # field = form.cleaned_data.get('database_fields')
                    # qualifier = form.cleaned_data.get('qualifiers')
                    field, qualifier = form_lookup(form=form)
                    operator = form.cleaned_data.get('query_operators')
                    query = form.cleaned_data.get('query')
                    if query != '' and query is not None:
                        kwargs = {
                            str('{db_table}__{qualifier}'.format(db_table=field,
                                                                 qualifier=qualifier)): str(query)
                        }
                        if operator == 'AND':
                            # queries.append(q & Q(**kwargs))
                            q = q & Q(**kwargs)
                        elif operator == 'OR':
                            q = q | Q(**kwargs)
                            # queries.append(q | Q(**kwargs))
                        elif operator == 'NOT':
                            q = q & ~Q(**kwargs)
                            # queries.append(q & ~Q(**kwargs))
                        else:
                            pass
            print('query', str(q))
            query_set = query_set.filter(q)
            print(len(query_set))
            # for query in queries:
            #     print('query', str(query))
            #     query_set.filter(query)
            #     print(len(query_set))
    else:
        # sample_form_set = sample_form_set_factory()
        database_form_set = database_formset_factory()
    return render(request,
                  'sequence_database/database_browse.html',
                  {
                      'form_set': database_form_set,
                      'date': database_date,
                      'database_result': query_set
                  })

from django_filters.views import FilterView
from django_tables2.views import SingleTableMixin
from django_tables2 import SingleTableView
from olc_webportalv2.sequence_database.tables import SequenceDataTable

class DatabaseFilterResults(SingleTableMixin, FilterView):

    model = SequenceData
    table_class = SequenceDataTable
    filterset_class = SequenceDatabaseFilter

@login_required
def database_filter_results(request):
    # id_list = (str(list(id_dict.keys()))).replace("'", "").replace("[", "").replace("]", "").replace(",", " ")
    table = SequenceDataTable(UniqueGenus.objects.all())
    return render(request,
                  'sequence_database/database_filter_results.html',
                  {
                      'table': table
                  })
    # template_name = 'olcwebportal_v2/templates/sequence_database/database_results.html',
    # fields = ['genus', 'species', 'mlst', 'mlstcc', 'rmlst', 'geneseekr', 'serovar', 'vtyper']
# # Uses database_result to filter db, and then loop through queryset to compile dictionary
# def id_sync(x):
#     id_dict = dict()
#     data_set = SequenceData.objects.filter(seqid__in=x)
#     for item in data_set:
#         if item.labid is not None:
#             labid_result = str(item.labid)
#         else:
#             labid_result = 'N/A'
#
#         if item.olnid is not None:
#             olnid_result = str(item.olnid)
#         else:
#             olnid_result = 'N/A'
#
#         id_dict.update({item.seqid: (labid_result, olnid_result)})
#     return id_dict
