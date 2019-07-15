from rest_framework import permissions, views, parsers, response, generics
from azure.storage.blob import BlockBlobService, BlobPermissions
from olc_webportalv2.cowbat.models import SequencingRun, DataFile
from olc_webportalv2.cowbat.tasks import run_cowbat_batch
from django.conf import settings
from django.http import JsonResponse, Http404
import os
import copy


class UploadView(views.APIView):
    parser_classes = (parsers.FileUploadParser, )
    permission_classes = (permissions.IsAuthenticated, )

    def get(self, request, *args, **kwargs):
        run_name = kwargs['run_name']
        container_name = run_name.lower().replace('_', '-')
        file_name = kwargs['filename']
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        # Check that a) file exists in blob storage and b) that it has non-zero size.
        if file_name.endswith('.bin'):
            file_name = os.path.join('InterOp', file_name)
        else:
            file_name = file_name
        file_exists = blob_client.exists(container_name=container_name, blob_name=file_name)
        if file_exists:
            blob = blob_client.get_blob_properties(container_name=container_name, blob_name=file_name)
            blob_size = blob.properties.content_length
        else:
            blob_size = 0
        return JsonResponse({'exists': file_exists, 'size': blob_size})

    def put(self, request, *args, **kwargs):
        run_name = kwargs['run_name']
        file_name = kwargs['filename']
        file_obj = request.data['file']
        container_name = run_name.lower().replace('_', '-')
        if not SequencingRun.objects.filter(run_name=run_name).exists():
            sequencing_run = SequencingRun.objects.create(run_name=run_name)
            sequencing_run.save()
        sequencing_run = SequencingRun.objects.get(run_name=run_name)
        # SampleSheet has data we need - read through it.
        if file_name == 'SampleSheet.csv':
            samplesheet_obj = copy.deepcopy(file_obj)  # If this isn't here, end up with 0 byte samplesheet upload.
            instance = DataFile(sequencing_run=sequencing_run,
                                data_file=samplesheet_obj)
            instance.save()
            with open('olc_webportalv2/media/{run_name}/SampleSheet.csv'.format(run_name=str(sequencing_run))) as f:
                lines = f.readlines()
            seqid_start = False
            seqid_list = list()
            realtime_dict = dict()
            # Sample plate column in SampleSheet should have Lab/Whatever other ID.
            # Store that data in a dictionary with SeqIDs as keys and LabIDs as values
            sample_plate_dict = dict()
            for i in range(len(lines)):
                if seqid_start:
                    seqid = lines[i].split(',')[0]
                    labid = lines[i].split(',')[2]
                    sample_plate_dict[seqid] = labid
                    try:
                        realtime = lines[i].rstrip().split(',')[9]
                    except IndexError:
                        realtime = ''
                    seqid_list.append(seqid)
                    if realtime == 'TRUE' or realtime == 'VRAI':
                        realtime_dict[seqid] = 'True'  # Not sure JSONField this gets stored in can handle bool
                    else:
                        realtime_dict[seqid] = 'False'
                if 'Sample_ID' in lines[i]:
                    seqid_start = True
            SequencingRun.objects.filter(pk=sequencing_run.pk).update(seqids=seqid_list,
                                                                      realtime_strains=realtime_dict,
                                                                      sample_plate=sample_plate_dict)

        # InterOp files will always have .bin extension, and need to be put into the InterOp folder
        if file_name.endswith('.bin'):
            blob_file_name = os.path.join('InterOp', file_name)
        else:
            blob_file_name = file_name
        blob_client = BlockBlobService(account_name=settings.AZURE_ACCOUNT_NAME,
                                       account_key=settings.AZURE_ACCOUNT_KEY)
        blob_client.create_container(container_name)
        blob_client.create_blob_from_bytes(container_name=container_name,
                                           blob_name=blob_file_name,
                                           blob=file_obj.read())

        # Do some stuff
        return response.Response(status=204)


class StartCowbatView(generics.RetrieveAPIView):
    permission_classes = (permissions.IsAuthenticated, )

    def get_queryset(self):
        run_name = self.kwargs['run_name']
        sequencing_run = SequencingRun.objects.get(run_name=run_name)
        return sequencing_run

    def retrieve(self, request, *args, **kwargs):
        try:
            sequencing_run = self.get_queryset()
        except SequencingRun.DoesNotExist:
            raise Http404
        if sequencing_run.status == 'Unprocessed':
            run_cowbat_batch.apply_async(queue='cowbat', args=(sequencing_run.pk, ))
            sequencing_run.status = 'Processing'
            sequencing_run.save()
            return JsonResponse({'status': 'Started assembly of run {}'.format(self.kwargs['run_name'])})
        else:
            return JsonResponse({'status': 'Did not start assembly for {run_name}. Status for {run_name} is '
                                           '{status}'.format(run_name=self.kwargs['run_name'],
                                                             status=sequencing_run.status)})


