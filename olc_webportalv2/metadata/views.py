from django.shortcuts import render, get_object_or_404, redirect
from olc_webportalv2.metadata.forms import MetaDataRequestForm, make_species_choice_list, make_genus_choice_list, \
    make_mlst_choice_list, make_rmlst_choice_list, make_serotype_choice_list
from olc_webportalv2.metadata.models import MetaDataRequest, SequenceData, LabID
from django.contrib.auth.decorators import login_required
from dal import autocomplete


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
    labidDict = LabID_sync_SeqID(metadata_result.seqids)
    return render(request,
                  'metadata/metadata_results.html',
                  {
                      'metadata_result': metadata_result, 'labidDict': labidDict
                  })


def LabID_sync_SeqID(seqid_list):
    labidDict = dict()
    for item in seqid_list:
        sequence_result = SequenceData.objects.get(seqid=item)
        if sequence_result.labid is not None:
            labid_result = LabID.objects.get(pk=sequence_result.labid.pk)
        else:
            labid_result = 'N/A'
        labidDict.update({sequence_result.seqid: str(labid_result)})
    return labidDict
