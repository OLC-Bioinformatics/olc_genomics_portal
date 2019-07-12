from rest_framework import permissions, views, parsers, response, generics
from azure.storage.blob import BlockBlobService, BlobPermissions
from olc_webportalv2.cowbat.models import SequencingRun
from olc_webportalv2.cowbat.tasks import run_cowbat_batch
from django.conf import settings
from django.http import JsonResponse
import os


class UploadView(views.APIView):
    parser_classes = (parsers.FileUploadParser, )
    permission_classes = (permissions.IsAuthenticated, )

    def put(self, request, *args, **kwargs):
        run_name = kwargs['run_name']
        file_name = kwargs['filename']
        file_obj = request.data['file']
        container_name = run_name.lower().replace('_', '-')
        if not SequencingRun.objects.filter(run_name=run_name).exists():
            sequencing_run = SequencingRun.objects.create(run_name=run_name)
            sequencing_run.save()
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
        sequencing_run = self.get_queryset()
        if sequencing_run.status == 'Unprocessed':
            run_cowbat_batch.apply_async(queue='cowbat', args=(sequencing_run.pk, ))
            sequencing_run.status = 'Processing'
            sequencing_run.save()
            return JsonResponse({'status': 'Started assembly of run {}'.format(self.kwargs['run_name'])})
        else:
            return JsonResponse({'status': 'Did not start assembly for {}'.format(self.kwargs['run_name'])})


