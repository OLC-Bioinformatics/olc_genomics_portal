"""
Views for API functions
"""

# Standard imports
import copy
import fnmatch
import os

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
from olc_webportalv2.common.storage_client import get_storage_client

import json
import logging
import posixpath
import re
import uuid
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from olc_webportalv2.api.authentication import DesktopTokenAuthentication
from olc_webportalv2.api.serializers import NanoporeFileSerializer, NanoporeRunSerializer
from olc_webportalv2.cowbat.models import NanoporeFile
from olc_webportalv2.cowbat.tasks import collect_nanopore_iteration
from olc_webportalv2.common.storage_client import StorageServiceError


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

        storage = get_storage_client()

        # If the file has a .bin extension, it goes in the InterOp folder
        if file_name.endswith('.bin'):
            file_name = os.path.join('InterOp', file_name)

        # Check if the file exists in the container
        file_exists = storage.object_exists(container_name, file_name)

        # If the file exists, calculate its size
        if file_exists:
            blob_size = storage.get_object(container_name, file_name).size
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

        storage = get_storage_client()

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

        storage.ensure_container(container_name)

        # Upload the file to blob storage
        storage.upload_bytes(
            container_name,
            blob_file_name,
            file_obj.read(),
            content_type=getattr(file_obj, "content_type", None),
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
            storage = get_storage_client()
            container_name = self.kwargs['run_name'].lower().replace('_', '-')
            blobs = storage.list_objects(container_name)
            blob_filenames.extend(blob.name for blob in blobs)
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
            sequencing_run.task_submitted_at = timezone.now()
            sequencing_run.task_queue = 'cowbat'
            sequencing_run.failure_stage = ''
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
            sequencing_run.task_submitted_at = timezone.now()
            sequencing_run.task_queue = 'cowbat'
            sequencing_run.failure_stage = ''
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


logger = logging.getLogger(__name__)


def _nanopore_request_id():
    return uuid.uuid4().hex


def _nanopore_results_container():
    """Return the configured container used for Nanopore results."""
    return getattr(
        settings,
        'NANOPORE_RESULTS_CONTAINER',
        'nanopore-results',
    )


def _nanopore_blob_read_url(blob_client, container_name, blob_name):
    """Return a short-lived, read-only URL for one result blob."""
    return blob_client.create_transfer_url(
        container_name,
        "download",
        name=blob_name,
        expires_in_seconds=900,
    )


def _nanopore_output_blob_candidates(run, output, manifest_blob_name=None):
    """Find an output in its iteration first, then historical run locations."""
    output_name = str(
        output.get('storage_blob_name')
        or output.get('blob_name')
        or output.get('path')
        or output.get('name')
        or ''
    ).replace('\\', '/').lstrip('/')
    if not output_name or '..' in output_name.split('/'):
        return []

    run_prefix = 'runs/{0}/'.format(run.run_name)
    # Never issue a URL for another run even if a manifest supplies a full path.
    if output_name.startswith('runs/') and not output_name.startswith(run_prefix):
        return []

    candidates = []

    def add(candidate):
        candidate = str(candidate or '').replace('\\', '/').lstrip('/')
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    # An immutable manifest lives in .../iterations/iteration-N/manifests/.
    # Scheduler outputs are relative to that iteration, not the run root.
    iteration_root = None
    if manifest_blob_name:
        manifest_name = str(manifest_blob_name).replace('\\', '/').lstrip('/')
        if manifest_name.startswith(run_prefix):
            before, separator, after = manifest_name.partition('/manifests/')
            if separator and after and before.startswith(run_prefix + 'iterations/'):
                iteration_root = before

    if not output_name.startswith('runs/'):
        relative_name = output_name
        if relative_name.startswith('output/'):
            relative_name = relative_name[len('output/'):]
        if iteration_root:
            add(posixpath.join(iteration_root, relative_name))
        add(posixpath.join(run_prefix, relative_name))
        add(output_name)  # Historical root-level result blobs.
        if relative_name != output_name:
            add(relative_name)
    else:
        add(output_name)
    return candidates


def _nanopore_enrich_result_manifest(
        blob_client, container_name, run, manifest_blob_name):
    """Load one result manifest and add read-only URLs to its outputs."""
    manifest = json.loads(
        blob_client.download_bytes(container_name, manifest_blob_name)
    )

    if not isinstance(manifest, dict):
        raise ValueError('Nanopore result manifest must be a JSON object.')
    try:
        result_run_id = int(manifest.get('run_id'))
    except (TypeError, ValueError):
        raise ValueError('Result manifest has an invalid run ID.')
    if result_run_id != run.pk or manifest.get('run_name') != run.run_name:
        raise ValueError('Result manifest belongs to a different run.')

    # Check the generation before creating any output SAS URLs.
    iteration_match = re.match(
        r'^runs/{0}/iterations/iteration-([0-9]+)/manifests/[^/]+$'.format(
            re.escape(run.run_name)
        ),
        str(manifest_blob_name),
    )
    if iteration_match:
        try:
            manifest_generation = int(manifest.get(
                'generation', manifest.get('iteration')
            ))
        except (TypeError, ValueError):
            raise ValueError('Result manifest has an invalid generation.')
        if manifest_generation != int(iteration_match.group(1)):
            raise ValueError('Result manifest generation does not match its path.')

    outputs = manifest.get('outputs') or []
    enriched_outputs = []

    for original_output in outputs:
        if not isinstance(original_output, dict):
            continue

        output = dict(original_output)
        resolved_blob_name = None
        for candidate in _nanopore_output_blob_candidates(
                run, output, manifest_blob_name):
            if blob_client.object_exists(container_name, candidate):
                resolved_blob_name = candidate
                break

        if resolved_blob_name:
            output['storage_blob_name'] = resolved_blob_name
            output['download_url'] = _nanopore_blob_read_url(
                blob_client,
                container_name,
                resolved_blob_name,
            )
            output['available'] = True
        else:
            output['available'] = False

        enriched_outputs.append(output)

    manifest['outputs'] = enriched_outputs
    return manifest


def _nanopore_run(request, run_pk):
    return get_object_or_404(SequencingRun, pk=run_pk, user=request.user)


def _validate_nanopore_path(path):
    path = str(path or '').replace('\\', '/')
    if not path or path.startswith('/') or '..' in path.split('/'):
        raise ValueError('Invalid relative path.')
    if not path.lower().endswith('.pod5'):
        raise ValueError('Only POD5 files are accepted.')
    return posixpath.normpath(path)


class NanoporeAPIView(views.APIView):
    authentication_classes = (
        DesktopTokenAuthentication, TokenAuthentication, SessionAuthentication
    )
    permission_classes = (permissions.IsAuthenticated,)


def _nanopore_name_storage_conflict(run_name):
    """Check all historical namespaces without creating or modifying blobs."""
    blob_client = get_storage_client()
    namespaces = (
        (getattr(settings, 'NANOPORE_RAW_CONTAINER', 'nanopore-runs'),
         '{0}/'.format(run_name)),
        (_nanopore_results_container(), '{0}/'.format(run_name)),
        (_nanopore_results_container(), 'runs/{0}/'.format(run_name)),
    )
    for container_name, prefix in namespaces:
        # A single blob is enough; never enumerate or download the whole run.
        try:
            objects = blob_client.list_objects(container_name, prefix=prefix)
        except StorageServiceError as exc:
            if exc.status_code == 404:
                continue
            raise
        if objects:
            return '{0}/{1}'.format(container_name, objects[0].name)
    return None


class NanoporeRunView(NanoporeAPIView):

    def post(self, request):
        request_id = _nanopore_request_id()
        serializer = NanoporeRunSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        run_name = serializer.validated_data.get('run_name')
        if not run_name:
            return Response({
                'error': 'A valid run name is required.',
                'field': 'run_name',
                'request_id': request_id,
            }, status=status.HTTP_400_BAD_REQUEST)
        if SequencingRun.objects.filter(run_name__iexact=run_name).exists():
            return Response({
                'error': 'A run with this name already exists.',
                'field': 'run_name',
                'request_id': request_id,
            }, status=status.HTTP_409_CONFLICT)
        try:
            conflict = _nanopore_name_storage_conflict(run_name)
        except Exception:
            logger.exception('Nanopore storage name check failed (request_id=%s)', request_id)
            return Response({
                'error': 'Could not verify run name availability in storage. Please retry later.',
                'field': 'run_name',
                'request_id': request_id,
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if conflict is not None:
            return Response({
                'error': 'Run name is already in use in storage; choose a new name.',
                'field': 'run_name',
                'request_id': request_id,
            }, status=status.HTTP_409_CONFLICT)
        try:
            with transaction.atomic():
                run = serializer.save()
        except IntegrityError:
            return Response({
                'error': 'A run with this name already exists.',
                'field': 'run_name',
                'request_id': request_id,
            }, status=status.HTTP_409_CONFLICT)
        except Exception:
            logger.exception(
                'Nanopore run creation failed (request_id=%s)', request_id
            )
            return Response({
                'error': 'Nanopore run could not be created.',
                'request_id': request_id,
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        nanopore_run = run.nanopore_run
        return Response({
            'run_id': run.pk,
            'run_name': run.run_name,
            'workflow_state': run.workflow_state,
            'status': nanopore_run.status,
            'instrument_name': nanopore_run.instrument_name,
            'instrument_type': nanopore_run.instrument_type,
            'source_directory': nanopore_run.source_directory,
            'dorado_model': nanopore_run.dorado_model,
            'reference_panel': nanopore_run.reference_panel,
            'barcode_kit': nanopore_run.barcode_kit,
            'barcode_values': nanopore_run.barcode_values,
            'sample_metadata': nanopore_run.sample_metadata,
            'request_id': request_id,
        }, status=status.HTTP_201_CREATED)


class NanoporeRunDetailView(NanoporeAPIView):
    def get(self, request, run_pk):
        run = _nanopore_run(request, run_pk)
        files = run.nanopore_files.all()
        nanopore_run = run.nanopore_run
        batches = run.nanopore_processing_batches.order_by('generation')
        reports = []
        report_blob_service = None
        for batch in batches:
            report = batch.report
            if not report and batch.report_url:
                if report_blob_service is None:
                    report_blob_service = get_storage_client()
                try:
                    published = report_blob_service.object_exists(
                        _nanopore_results_container(),
                        batch.report_url,
                    )
                except StorageServiceError as exc:
                    if exc.status_code == 404:
                        published = False
                    else:
                        logger.exception(
                            'Could not check Nanopore result run=%s generation=%s',
                            run.pk,
                            batch.generation,
                        )
                        published = False
                if published:
                    try:
                        candidate = _nanopore_enrich_result_manifest(
                            report_blob_service, _nanopore_results_container(),
                            run, batch.report_url,
                        )
                        candidate_generation = int(candidate.get(
                            'generation', candidate.get('iteration')
                        ))
                        if candidate_generation == batch.generation:
                            report = candidate
                    except (TypeError, ValueError):
                        logger.warning('Ignoring mismatched result run=%s generation=%s',
                                       run.pk, batch.generation)
                    except Exception:
                        logger.exception('Could not verify result run=%s generation=%s',
                                         run.pk, batch.generation)
            if report:
                reports.append({
                    'batch_id': batch.pk,
                    'generation': batch.generation,
                    'iteration': batch.generation,
                    'published_at': batch.last_completed_at,
                    'report': report,
                    'report_url': batch.report_url,
                })
        return Response({
            'run_id': run.pk,
            'run_name': run.run_name,
            'workflow_state': run.workflow_state,
            'status': run.status,
            'nanopore_status': nanopore_run.status,
            'instrument_name': nanopore_run.instrument_name,
            'instrument_type': nanopore_run.instrument_type,
            'source_directory': nanopore_run.source_directory,
            'dorado_model': nanopore_run.dorado_model,
            'reference_panel': nanopore_run.reference_panel,
            'barcode_kit': nanopore_run.barcode_kit,
            'barcode_values': nanopore_run.barcode_values,
            'sample_metadata': nanopore_run.sample_metadata,
            'scheduler_input_sha256': nanopore_run.scheduler_input_sha256,
            'scheduler_metadata_sha256': nanopore_run.scheduler_metadata_sha256,
            'manifest_sha256': getattr(
                run.nanopore_manifests.order_by('-generation').first(),
                'sha256', None
            ),
            'processing': self._processing_payload(run, reports),
            'files': NanoporeFileSerializer(files, many=True).data,
            'request_id': _nanopore_request_id(),
        })

    @staticmethod
    def _processing_payload(run, reports=None):
        """Return aggregate Nanopore processing state for one run."""
        batches = run.nanopore_processing_batches.all()
        batch_counts = {}
        for batch_status in (
                'pending', 'submitting', 'submitted', 'running',
                'published', 'complete', 'failed'):
            batch_counts[batch_status] = batches.filter(
                status=batch_status
            ).count()

        task_owner_batch = batches.filter(
            status__in=('submitting', 'submitted', 'running'),
            azure_job_id__gt='',
        ).order_by('generation').first()
        current_batch = batches.filter(
            status__in=(
                'pending', 'submitting', 'submitted', 'running',
                'published',
            )
        ).order_by('-generation').first()
        failed_batch = batches.filter(
            status='failed'
        ).order_by('-generation').first()
        latest_batch = batches.order_by('-generation').first()
        display_batch = current_batch or failed_batch or latest_batch

        waiting_file_count = run.nanopore_files.filter(
            status='uploaded', processing_batch__isnull=True
        ).count()

        if display_batch is None and waiting_file_count == 0:
            return None

        status_messages = {
            "pending": "Waiting for the processing task to start.",
            "submitting": "Submitting the processing task to Azure Batch.",
            "submitted": "Processing task submitted; waiting for a worker.",
            "running": "Nanopore processing is running.",
            "published": "The next iteration is queued for the active processing task.",
            "complete": "Nanopore processing is complete.",
            "failed": "Nanopore processing failed.",
            "collecting": "Waiting for the upload collection window.",
        }

        if current_batch is not None:
            processing_status = current_batch.status
        elif failed_batch is not None:
            processing_status = 'failed'
        elif waiting_file_count:
            processing_status = 'collecting'
        else:
            processing_status = getattr(
                display_batch, 'status', 'pending'
            )

        error = getattr(display_batch, 'error', '') or ''
        message = error or status_messages.get(
            processing_status,
            'Nanopore processing status is unavailable.',
        )

        return {
            'status': processing_status,
            'retryable': (
                processing_status == 'submitting' and bool(error)
            ),
            'retry_count': run.task_retry_count,
            'retry_limit': getattr(
                settings, 'NANOPORE_SUBMISSION_MAX_RETRIES', 8
            ),
            'message': message,
            'job_id': getattr(task_owner_batch, 'azure_job_id', '') or '',
            'task_id': getattr(task_owner_batch, 'azure_task_id', '') or '',
            'results_container': getattr(
                settings,
                'NANOPORE_RESULTS_CONTAINER',
                'nanopore-results',
            ),
            'results_prefix': 'runs/{0}'.format(run.run_name),
            'latest_manifest_path': (
                'runs/{0}/manifests/latest.json'.format(run.run_name)
            ),
            'exit_code': getattr(display_batch, 'exit_code', None),
            'error': error,
            'submitted_at': getattr(display_batch, 'submitted_at', None),
            'started_at': getattr(display_batch, 'started_at', None),
            'finished_at': getattr(display_batch, 'finished_at', None),
            'batch_id': getattr(display_batch, 'pk', None),
            'active_generation': getattr(
                current_batch, 'generation', None
            ),
            'current_generation': getattr(
                current_batch, 'generation', None
            ),
            'task_owner_generation': getattr(
                task_owner_batch, 'generation', None
            ),
            'failed_generation': getattr(
                failed_batch, 'generation', None
            ),
            'batch_counts': batch_counts,
            'waiting_file_count': waiting_file_count,
            'processed_pod5_count': sum(
                batch.processed_pod5_count for batch in batches
            ),
            'pod5_count': run.nanopore_files.count(),
            'reports': reports or [],
        }


class NanoporeLatestResultView(NanoporeAPIView):
    """Return the latest committed Nanopore result and output URLs."""

    def get(self, request, run_pk):
        run = _nanopore_run(request, run_pk)
        container_name = _nanopore_results_container()
        latest_blob_name = 'runs/{0}/manifests/latest.json'.format(
            run.run_name
        )
        blob_client = get_storage_client()
        if not blob_client.object_exists(container_name, latest_blob_name):
            return Response(
                {'error': 'Results are not published yet.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            latest_payload = json.loads(
                blob_client.download_bytes(container_name, latest_blob_name)
            )
        except (TypeError, ValueError):
            latest_payload = {}

        if not isinstance(latest_payload, dict):
            latest_payload = {}
        try:
            latest_run_id = int(latest_payload.get('run_id'))
        except (TypeError, ValueError):
            latest_run_id = None
        if latest_run_id != run.pk or latest_payload.get('run_name') != run.run_name:
            return Response({'error': 'Latest results belong to another run.'},
                            status=status.HTTP_409_CONFLICT)
        generation = latest_payload.get('generation')
        if generation is None:
            generation = latest_payload.get('iteration')

        processing_batch = None
        try:
            generation = int(generation)
        except (TypeError, ValueError):
            generation = None

        if generation is not None:
            processing_batch = (
                run.nanopore_processing_batches
                .filter(generation=generation)
                .first()
            )
        if processing_batch is None:
            return Response({'error': 'Latest iteration is not registered.'},
                            status=status.HTTP_404_NOT_FOUND)

        response_payload = {
            'container': container_name,
            'blob_name': latest_blob_name,
            'url': _nanopore_blob_read_url(
                blob_client,
                container_name,
                latest_blob_name,
            ),
        }

        if processing_batch is not None and processing_batch.report_url:
            try:
                response_payload['generation'] = processing_batch.generation
                response_payload['report'] = _nanopore_enrich_result_manifest(
                    blob_client,
                    container_name,
                    run,
                    processing_batch.report_url,
                )
                result_generation = int(response_payload['report'].get(
                    'generation', response_payload['report'].get('iteration')
                ))
                if result_generation != processing_batch.generation:
                    raise ValueError('Latest result generation mismatch.')
                response_payload['report_blob_name'] = (
                    processing_batch.report_url
                )
            except Exception:
                logger.exception(
                    'Could not enrich latest Nanopore result run=%s '
                    'generation=%s',
                    run.pk,
                    processing_batch.generation,
                )
                return Response({'error': 'Latest result could not be verified.'},
                                status=status.HTTP_502_BAD_GATEWAY)

        return Response(response_payload)


class NanoporeIterationResultView(NanoporeAPIView):
    """Return one immutable iteration result with output download URLs."""

    def get(self, request, run_pk, generation):
        run = _nanopore_run(request, run_pk)
        processing_batch = get_object_or_404(
            run.nanopore_processing_batches,
            generation=generation,
        )
        blob_name = processing_batch.report_url
        container_name = _nanopore_results_container()
        blob_client = get_storage_client()
        if not blob_name or not blob_client.object_exists(
                container_name, blob_name):
            return Response(
                {'error': 'Iteration results are not published yet.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            report = _nanopore_enrich_result_manifest(
                blob_client,
                container_name,
                run,
                blob_name,
            )
        except (TypeError, ValueError):
            logger.exception(
                'Invalid Nanopore result manifest run=%s generation=%s',
                run.pk,
                generation,
            )
            return Response(
                {'error': 'Iteration result manifest is invalid.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception:
            logger.exception(
                'Could not load Nanopore result manifest run=%s '
                'generation=%s',
                run.pk,
                generation,
            )
            return Response(
                {'error': 'Iteration result manifest could not be loaded.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        try:
            result_generation = int(report.get('generation', report.get('iteration')))
        except (TypeError, ValueError):
            result_generation = None
        if result_generation != int(generation):
            return Response({'error': 'Result generation does not match.'},
                            status=status.HTTP_409_CONFLICT)
        return Response({
            'generation': generation,
            'container': container_name,
            'blob_name': blob_name,
            'url': _nanopore_blob_read_url(
                blob_client,
                container_name,
                blob_name,
            ),
            'report': report,
            'request_id': _nanopore_request_id(),
        })


class NanoporeFilePrepareView(NanoporeAPIView):
    def post(self, request, run_pk):
        run = _nanopore_run(request, run_pk)
        if run.workflow_state not in ('created', 'uploading', 'processing'):
            return Response({'error': 'Run is not accepting uploads.'},
                            status=status.HTTP_409_CONFLICT)
        try:
            relative_path = _validate_nanopore_path(
                request.data.get('relative_path') or request.data.get('filename')
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            size_bytes = int(request.data.get('size_bytes'))
        except (TypeError, ValueError):
            return Response({'error': 'size_bytes must be an integer.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if size_bytes < 1:
            return Response({'error': 'size_bytes must be positive.'},
                            status=status.HTTP_400_BAD_REQUEST)
        raw_container = getattr(settings, 'NANOPORE_RAW_CONTAINER',
                                'nanopore-runs')
        storage_prefix = run.run_name
        blob_name = '{0}/{1}/input/pod5/{2}'.format(
            raw_container, storage_prefix, relative_path
        )
        nanopore_file, created = NanoporeFile.objects.get_or_create(
            sequencing_run=run,
            relative_path=relative_path,
            defaults={'blob_name': blob_name, 'size_bytes': size_bytes}
        )
        if not created and nanopore_file.size_bytes != size_bytes:
            return Response({'error': 'File size conflicts with existing manifest.'},
                            status=status.HTTP_409_CONFLICT)
        blob_client = get_storage_client()
        blob_client.ensure_container(raw_container)
        object_name = '{0}/input/pod5/{1}'.format(
            storage_prefix, relative_path
        )
        upload_url = blob_client.create_transfer_url(
            raw_container,
            'write',
            name=object_name,
            expires_in_seconds=1800,
        )
        if run.workflow_state == 'created':
            run.workflow_state = 'uploading'
            run.save(update_fields=['workflow_state'])
        return Response({
            'file_id': nanopore_file.pk,
            'upload_url': upload_url,
            'blob_name': nanopore_file.blob_name,
            'request_id': _nanopore_request_id(),
        })


class NanoporeFileCompleteView(NanoporeAPIView):
    """Verify an uploaded POD5 file and schedule iteration collection."""

    @staticmethod
    def _schedule_collection(run_pk):
        countdown = getattr(
            settings,
            'NANOPORE_COLLECTION_WINDOW_SECONDS',
            60,
        )
        collect_nanopore_iteration.apply_async(
            queue='cowbat',
            args=(run_pk,),
            countdown=countdown,
        )

    def post(self, request, run_pk, file_pk):
        run = _nanopore_run(request, run_pk)
        nanopore_file = NanoporeFile.objects.get(
            pk=file_pk,
            sequencing_run=run,
        )
        if run.workflow_state in ('stopping', 'complete', 'error'):
            return Response(
                {'error': 'Run is no longer accepting files.'},
                status=status.HTTP_409_CONFLICT,
            )

        if nanopore_file.status == 'uploaded':
            if nanopore_file.processing_batch_id is None:
                self._schedule_collection(run.pk)
            payload = NanoporeFileSerializer(nanopore_file).data
            payload['request_id'] = _nanopore_request_id()
            return Response(payload)

        raw_container = getattr(
            settings,
            'NANOPORE_RAW_CONTAINER',
            'nanopore-runs',
        )
        try:
            properties = get_storage_client().get_object(
                raw_container,
                nanopore_file.blob_name.replace(
                    '{0}/'.format(raw_container), '', 1
                ),
            )
        except StorageServiceError as exc:
            if exc.status_code != 404:
                logger.exception(
                    'Could not verify Nanopore upload run=%s file=%s',
                    run.pk,
                    nanopore_file.pk,
                )
                return Response(
                    {'error': 'Could not verify uploaded blob; please retry.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(
                {'error': 'Uploaded blob was not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if properties.size != nanopore_file.size_bytes:
            nanopore_file.status = 'error'
            nanopore_file.error = (
                'Uploaded size does not match manifest.'
            )
            nanopore_file.save(update_fields=['status', 'error'])
            return Response(
                {'error': nanopore_file.error},
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            run = SequencingRun.objects.select_for_update().get(pk=run.pk)
            nanopore_file = NanoporeFile.objects.select_for_update().get(
                pk=nanopore_file.pk,
                sequencing_run=run,
            )
            nanopore_file.status = 'uploaded'
            nanopore_file.uploaded_at = timezone.now()
            nanopore_file.attempt_count += 1
            nanopore_file.error = ''
            nanopore_file.save(update_fields=[
                'status',
                'uploaded_at',
                'attempt_count',
                'error',
            ])

            nanopore_run = run.nanopore_run
            nanopore_run.status = 'processing'
            nanopore_run.last_file_seen_at = timezone.now()
            nanopore_run.save(update_fields=[
                'status',
                'last_file_seen_at',
            ])
            run.workflow_state = 'processing'
            run.save(update_fields=['workflow_state'])

            transaction.on_commit(
                lambda run_pk=run.pk: self._schedule_collection(run_pk)
            )

        payload = NanoporeFileSerializer(nanopore_file).data
        payload['request_id'] = _nanopore_request_id()
        return Response(payload)


class NanoporeFinalizeView(NanoporeAPIView):
    """Close upload intake and drain all completed POD5 files."""

    def post(self, request, run_pk):
        with transaction.atomic():
            run = get_object_or_404(
                SequencingRun.objects.select_for_update(),
                pk=run_pk,
                user=request.user,
            )
            nanopore_run = run.nanopore_run

            if not run.nanopore_files.exists():
                return Response(
                    {'error': 'No POD5 files have been registered.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if run.nanopore_files.exclude(status='uploaded').exists():
                return Response(
                    {'error': 'All registered files must be uploaded.'},
                    status=status.HTTP_409_CONFLICT,
                )

            if run.workflow_state not in ('stopping', 'complete'):
                run.workflow_state = 'stopping'
                run.save(update_fields=['workflow_state'])
                nanopore_run.termination_requested_at = timezone.now()
                nanopore_run.finalized_at = timezone.now()
                nanopore_run.status = 'stopping'
                nanopore_run.save(update_fields=[
                    'termination_requested_at',
                    'finalized_at',
                    'status',
                ])
                transaction.on_commit(
                    lambda run_pk=run.pk: (
                        collect_nanopore_iteration.apply_async(
                            queue='cowbat',
                            args=(run_pk,),
                            kwargs={'force': True},
                        )
                    )
                )

            processing_batch = (
                run.nanopore_processing_batches
                .order_by('-generation')
                .first()
            )
            latest_manifest = (
                run.nanopore_manifests
                .order_by('-generation')
                .first()
            )
            response_payload = {
                'run_id': run.pk,
                'workflow_state': run.workflow_state,
                'manifest_sha256': getattr(
                    latest_manifest,
                    'sha256',
                    None,
                ),
                'processing_status': getattr(
                    processing_batch,
                    'status',
                    'pending',
                ),
                'request_id': _nanopore_request_id(),
            }
        return Response(response_payload)
