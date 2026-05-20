"""
Views for API functions
"""

# Standard imports
import copy
import fnmatch
import os

# Third party imports
from azure.common import AzureMissingResourceHttpError
from azure.storage.blob import BlockBlobService

from django.conf import settings
from django.http import JsonResponse, Http404

from rest_framework import permissions, views, parsers, response, generics, \
    status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response


# Local imports
from olc_webportalv2.api.serializers import SequencingRunSerializer
from olc_webportalv2.api.methods import send_email
from olc_webportalv2.cowbat.models import SequencingRun, DataFile
from olc_webportalv2.cowbat.tasks import run_cowbat_batch
from olc_webportalv2.cowbat.utils import normalize_run_name


class UploadView(views.APIView):
    """
    Handles file upload API requests
    """
    parser_classes = (parsers.FileUploadParser, )
    permission_classes = (permissions.IsAuthenticated, )

    def get(self, _, **kwargs):
        """
        Extract the run name and filename from the URL kwargs and check if the
        file exists in the blob storage. Return a JSON response with the
        results.
        """
        # Extract the run name from the keyword arguments
        run_name = kwargs['run_name']

        # Ensure that the container name fits the appropriate naming scheme
        container_name = run_name.lower().replace('_', '-')

        # Get the file name from the keyword arguments
        file_name = kwargs['filename']

        # Create a blob client to allow Azure file operations
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )

        # If the file has a .bin extension, it goes in the InterOp folder
        if file_name.endswith('.bin'):
            file_name = os.path.join('InterOp', file_name)

        # Check if the file exists in the container
        file_exists = blob_client.exists(
            container_name=container_name,
            blob_name=file_name
        )

        # If the file exists, calculate its size
        if file_exists:
            blob = blob_client.get_blob_properties(
                container_name=container_name,
                blob_name=file_name
            )
            blob_size = blob.properties.content_length
        else:
            blob_size = 0
        return JsonResponse({'exists': file_exists, 'size': blob_size})

    def put(self, request, **kwargs):
        """
        Handles file uploads and saves them to the blob storage. Returns a JSON
        response with the results.
        """
        # Extract the run name from the keyword arguments
        run_name = kwargs['run_name']

        # Ensure that the container name fits the appropriate naming scheme
        container_name = run_name.lower().replace('_', '-')

        # Get the file name from the keyword arguments
        file_name = kwargs['filename']

        # Create a blob client to allow Azure file operations
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )

        # If the file has a .bin extension, it goes in the InterOp folder
        if file_name.endswith('.bin'):
            file_name = os.path.join('InterOp', file_name)

        # Get the file contents from the request
        file_obj = request.data['file']

        # Create the SequencingRun model entry if it does not already exist
        if not SequencingRun.objects.filter(run_name=run_name).exists():
            sequencing_run = SequencingRun.objects.create(run_name=run_name)
            sequencing_run.save()
        sequencing_run = SequencingRun.objects.get(run_name=run_name)

        # SampleSheet has data we need - read through it.
        if file_name == 'SampleSheet.csv':
            # If this isn't here, end up with 0 byte samplesheet upload.
            samplesheet_obj = copy.deepcopy(file_obj)

            # Initialize a DataFile instance linking it to a sequencing run
            # and assigning the sample sheet object.
            instance = DataFile(
                sequencing_run=sequencing_run,
                data_file=samplesheet_obj
            )
            instance.save()

            # Open the data file associated with the instance from the
            # MEDIA_ROOT directory and read its lines.
            with open(os.path.join(
                    settings.MEDIA_ROOT,
                    instance.data_file.name
                    ), encoding='utf-8') as f:
                lines = f.readlines()

            # Initialise variables to store the SeqIDs, sample plate data,
            # and realtime strains
            seqid_start = False
            seqid_list = []
            realtime_dict = {}

            # Sample plate column in SampleSheet should have Lab/Whatever
            # other ID. Store that data in a dictionary with SeqIDs as keys
            # and LabIDs as values
            sample_plate_dict = dict()
            for i, _ in enumerate(lines):

                # Ensure that the sample information is present in the lines
                if seqid_start:

                    # Extract the SEQID and LABID from the sample sheet
                    seqid = lines[i].split(',')[0]
                    labid = lines[i].split(',')[2]

                    # Update the dictionary with the SEQID and LABID
                    sample_plate_dict[seqid] = labid

                    # Attempt to extract the realtime column information
                    try:
                        realtime = lines[i].rstrip().split(',')[9]
                    # Set the realtime variable to an empty string on an
                    # IndexError
                    except IndexError:
                        realtime = ''

                    # Update the list of SEQIDs
                    seqid_list.append(seqid)

                    # Since JSONField may not automatically interpret Python
                    # booleans, there are explicitly stored as strings
                    if realtime in ['TRUE', 'VRAI']:
                        realtime_dict[seqid] = 'True'
                    else:
                        realtime_dict[seqid] = 'False'

                # The sample information follows the line with 'Sample_ID' in
                # the first column
                if 'Sample_ID' in lines[i]:
                    # Update the seqid_start variable to indicate that sample
                    # information is to be processed
                    seqid_start = True

            # Update the model with the information parsed from the
            # sample sheet
            sequencing_run.seqids = seqid_list
            sequencing_run.realtime_strains = realtime_dict
            sequencing_run.sample_plate = sample_plate_dict
            sequencing_run.save()

        # InterOp files will always have .bin extension, and need to be put
        # into the InterOp folder
        if file_name.endswith('.bin'):
            blob_file_name = os.path.join('InterOp', file_name)
        else:
            blob_file_name = file_name

        # Create a blob client to handle Azure file operations
        blob_client = BlockBlobService(
            account_name=settings.AZURE_ACCOUNT_NAME,
            account_key=settings.AZURE_ACCOUNT_KEY
        )

        # Create the container if required
        blob_client.create_container(container_name)

        # Upload the file to blob storage
        blob_client.create_blob_from_bytes(
            container_name=container_name,
            blob_name=blob_file_name,
            blob=file_obj.read()
        )

        # Do some stuff
        return response.Response(status=204)


class StartCowbatView(generics.RetrieveAPIView):
    """
    Starts the COWBAT assembly process
    """
    permission_classes = (permissions.IsAuthenticated, )

    def get_queryset(self):
        """
        Uses the supplied run name from the GET call to find the appropriate
        sequencing run model
        """
        run_name = self.kwargs['run_name']
        sequencing_run = SequencingRun.objects.get(run_name=run_name)
        return sequencing_run

    def retrieve(self, _):
        """
        Retrieves the run name from the GET queryset. Ensures all forward and
        reverse reads are present. Starts the assembly
        """
        try:
            sequencing_run = self.get_queryset()
        except SequencingRun.DoesNotExist as exc:
            raise Http404 from exc
        if sequencing_run.status == 'Unprocessed':
            blob_filenames = []
            blob_client = BlockBlobService(
                account_name=settings.AZURE_ACCOUNT_NAME,
                account_key=settings.AZURE_ACCOUNT_KEY
            )
            container_name = self.kwargs['run_name'].lower().replace('_', '-')
            blobs = blob_client.list_blobs(container_name=container_name)
            for blob in blobs:
                blob_filenames.append(blob.name)
            all_files_present = True
            for seqid in sequencing_run.seqids:
                forward_reads = fnmatch.filter(blob_filenames, seqid + '*_R1*')
                reverse_reads = fnmatch.filter(blob_filenames, seqid + '*_R2*')
                if len(forward_reads) != 1 or len(reverse_reads) != 1:
                    all_files_present = False

            if all_files_present is False:
                return JsonResponse(
                    {
                        'status': 'Some files were missing. Could not '
                        'start assembly.'
                    }
                )
            run_cowbat_batch.apply_async(
                queue='cowbat', args=(sequencing_run.pk, )
            )
            sequencing_run.status = 'Processing'
            sequencing_run.save()
            return JsonResponse(
                {
                    'status': 'Started assembly of run {}'.format(
                        self.kwargs['run_name'])
                }
            )
        return JsonResponse(
            {
                'status': 'Did not start assembly for {run_name}. Status '
                'for {run_name} is {status}'.format(
                    run_name=self.kwargs['run_name'],
                    status=sequencing_run.status)
            }
        )


class ResearchAssemblyView(generics.GenericAPIView):
    """
    API view to initiate assembly process for research sequencing runs.
    Accepts data through POST request, validates it using
    SequencingRunSerializer, and processes it accordingly.
    """
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = SequencingRunSerializer

    def get(self, _):
        """
        Returns a simple message indicating the purpose of this endpoint.
        """
        return Response({
            "message": "This endpoint accepts POST requests to initiate the "
            "assembly process for research sequencing runs."
        })

    def post(self, request):
        """
        Handles POST request to initiate assembly process for a given
        run_name using the provided data.
        """
        # Existing blob client setup and container checks remain unchanged

        # Create a serializer using the request data
        serializer = self.get_serializer(data=request.data)

        # Validate the inputs
        if serializer.is_valid():
            # Create the sequencing run
            sequencing_run = serializer.save()

            # Initialize response data with the status message
            response_data = {
                'status': 'Started assembly of run {run}'.format(
                    run=sequencing_run.run_name
                )}

            # Run COWBAT on the files in blob storage
            run_cowbat_batch.apply_async(
                queue='cowbat', args=(sequencing_run.pk,)
            )

            # Update the model status
            sequencing_run.status = 'Processing'
            sequencing_run.save()

            # Return the response that the run has successfully started
            return Response(response_data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class EmailRelayView(views.APIView):
    """
    Accepts email relay requests from a local shim and forwards the
    payload to the internal send_email helper.
    """
    permission_classes = (permissions.AllowAny,)
    parser_classes = (parsers.JSONParser,)

    REQUIRED_FIELDS = ('subject', 'body', 'recipient')
    SECRET_ENV = 'EMAIL_RELAY_SECRET'
    SECRET_HEADER = 'HTTP_X_EMAIL_RELAY_SECRET'

    def _get_expected_secret(self):
        return os.environ.get(self.SECRET_ENV)

    def _validate_secret(self, request):
        expected = self._get_expected_secret()
        if not expected:
            return False
        return request.META.get(self.SECRET_HEADER) == expected

    def post(self, request):
        if not self._validate_secret(request):
            return Response(
                {
                    'status': 'unauthorized',
                    'error': 'Missing or invalid X-Email-Relay-Secret header.'
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        missing_fields = [
            field for field in self.REQUIRED_FIELDS
            if field not in request.data
        ]
        if missing_fields:
            return Response(
                {
                    'status': 'failure',
                    'error': 'Missing required fields.',
                    'fields': missing_fields,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            send_email(
                subject=request.data['subject'],
                body=request.data['body'],
                recipient=request.data['recipient'],
                sender_name=request.data.get('sender_name'),
            )
        except Exception as exc:
            return Response(
                {
                    'status': 'failure',
                    'error': 'Email relay failed: {}'.format(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'status': 'success', 'message': 'Email forwarded to send_email.'}
        )


class SequencingRunViewSet(viewsets.ModelViewSet):
    """
    CRUD + existence check for sequencing runs.
    """

    queryset = SequencingRun.objects.all().order_by("-id")
    serializer_class = SequencingRunSerializer
    lookup_field = "run_name"  # optional; keep pk if you prefer

    def get_queryset(self):
        """
        Allow filtering by ?run_name=<value> (raw, not yet normalized).
        """
        qs = super(SequencingRunViewSet, self).get_queryset()
        run_name = self.request.query_params.get("run_name")
        if run_name:
            norm = normalize_run_name(run_name)
            qs = qs.filter(run_name=norm)
        return qs

    @action(detail=False, methods=["get"], url_path="exists")
    def exists(self, request):
        """
        /research_assembly/exists?run_name=<value>
        Always returns 200.

        Response:
            { 'run_name': <normalized>, 'exists': true|false }
        """
        raw = request.query_params.get("run_name")
        if not raw:
            return Response(
                {"error": "run_name query param required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        norm = normalize_run_name(raw)
        present = SequencingRun.objects.filter(run_name=norm).exists()
        return Response({"run_name": norm, "exists": present})
