from django.shortcuts import render, get_object_or_404, redirect
from olc_webportalv2.metadata.forms import MetaDataRequestForm
from olc_webportalv2.metadata.models import MetaDataRequest, SequenceData, LabID
from django.contrib.auth.decorators import login_required

# Create your views here.
@login_required
def metadata_home(request):
    form = MetaDataRequestForm()
    if request.method == 'POST':
        form = MetaDataRequestForm(request.POST)
        if form.is_valid():
            quality = form.cleaned_data.get('quality')
            genus = form.cleaned_data.get('genus')
            exclude = form.cleaned_data.get('everything_but')
            sequence_data_matching_query = SequenceData.objects.all()
            if quality == 'Fail':
                if exclude:
                    sequence_data_matching_query = sequence_data_matching_query.exclude(genus__iexact=genus)
                else:
                    sequence_data_matching_query = sequence_data_matching_query.filter(genus__iexact=genus)
            elif quality == 'Pass':
                if exclude:
                    sequence_data_matching_query = sequence_data_matching_query.exclude(genus__iexact=genus).exclude(quality='Fail')
                else:
                    sequence_data_matching_query = sequence_data_matching_query.filter(genus__iexact=genus).exclude(quality='Fail')
            elif quality == 'Reference':
                if exclude:
                    sequence_data_matching_query = sequence_data_matching_query.exclude(genus__iexact=genus).filter(quality='Reference')
                else:
                    sequence_data_matching_query = sequence_data_matching_query.filter(genus__iexact=genus).filter(quality='Reference')
            seqid_list = list()
            for sequence_data in sequence_data_matching_query:
                seqid_list.append(sequence_data.seqid)

            metadata_request = MetaDataRequest.objects.create(seqids=seqid_list)
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

def LabID_sync_SeqID(x):
    labidDict ={}
    for y in x:
        sequence_result = SequenceData.objects.get(seqid=y)
        if sequence_result.labid is not None:
            labid_result = LabID.objects.get(pk=sequence_result.labid.pk)
        else:
            labid_result = 'N/A'
        labidDict.update({sequence_result.seqid: str(labid_result)})
    return labidDict
