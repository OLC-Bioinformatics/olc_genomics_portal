#!/usr/bin/env python

"""Background tasks for GeneSeekr and related analyses."""

# Standard library imports
import os
import posixpath
import shlex
import shutil
import smtplib
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from time import sleep

# Third-party imports
from celery import shared_task
from sentry_sdk import capture_exception

# Local imports
from olc_webportalv2.common.benchmarks import (
    BENCHMARK_CONTAINER_NAME,
    benchmark_blob_name,
    normalise_benchmark_name,
)
from olc_webportalv2.common.methods import generic_api_submit
from olc_webportalv2.common.storage_client import get_storage_client
from olc_webportalv2.cowbat.tasks import generate_download_link
from olc_webportalv2.geneseekr.methods import zip_files
from olc_webportalv2.geneseekr.models import (
    AMRAzureRequest,
    AMRSummary,
    GeneSeekrAzureRequest,
    GeneSeekrRequest,
    NearestNeighbors,
    NearNeighborDetail,
    ProkkaAzureRequest,
    ProkkaRequest,
    Tree,
    TreeAzureRequest,
)
from olc_webportalv2.metadata.models import SequenceData


@shared_task
def run_prokka(prokka_request_pk):
    """Submit a Prokka annotation request to Azure Batch.

    Args:
        prokka_request_pk: Primary key of the Prokka request.

    """
    prokka_request = ProkkaRequest.objects.get(pk=prokka_request_pk)
    try:
        container_name = f'prokka-{prokka_request_pk}'
        seqids = prokka_request.seqids or []
        other_files = prokka_request.other_input_files or []
        command_parts = ['source $CONDA/activate /envs/prokka']
        for seqid in seqids:
            command_parts.append(
                f'prokka --outdir $AZ_BATCH_NODE_MOUNTS_DIR/'
                f'{container_name}/{seqid} --prefix {seqid} --cpus 8 '
                f'sequences/{seqid}.fasta'
            )
        for other_file in other_files:
            file_name = os.path.basename(other_file)
            prefix = os.path.splitext(file_name)[0]
            command_parts.append(
                f'prokka --outdir "$AZ_BATCH_NODE_MOUNTS_DIR"/'
                f'{shlex.quote(other_file)} --prefix '
                f'{shlex.quote(prefix)} --cpus 8 '
                f'sequences/{shlex.quote(file_name)}'
            )
        cmd = ' && '.join(command_parts)
        input_file_pattern = [
            f'processed-data/{seqid}.fasta ' for seqid in seqids
        ]

        # Submit the request to the AzureBatch service
        generic_api_submit(
            command=cmd,
            container_name=container_name,
            input_file_pattern=input_file_pattern,
            vm_size='Standard_D8s_v3',
            unique_id='FoodPort',
        )

        ProkkaAzureRequest.objects.create(
            prokka_request=prokka_request, exit_code_file='NA'
        )
    except Exception as e:
        capture_exception(e)
        prokka_request.status = 'Error'
        prokka_request.save()


@shared_task
def run_amr_summary(amr_summary_pk):
    """Submit an antimicrobial-resistance summary to Azure Batch.

    Args:
        amr_summary_pk: Primary key of the AMR summary request.

    """
    amr_summary_request = AMRSummary.objects.get(pk=amr_summary_pk)
    try:
        container_name = f'amrsummary-{amr_summary_pk}'
        seqids = amr_summary_request.seqids or []
        command = (
            'export LC_ALL=C.UTF-8 && export LANG=C.UTF-8 && '
            'source $CONDA/activate /envs/cowbat && '
            'GeneSeekr blastn -u '
            f'-s $AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/sequences '
            '-t /datadrive/0.5.0.23/resfinder '
            f'-r $AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/sequences/reports '
            '-A && '
            'python -m genemethods.assemblypipeline.mobrecon '
            f'-s $AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/sequences '
            '-r /datadrive/0.5.0.23 && '
            f'mv $AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/sequences '
            f'$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/'
        )
        input_file_pattern = [
            f'processed-data/{seqid}.fasta ' for seqid in seqids
        ]

        # Submit the command to the AzureBatch service
        generic_api_submit(
            command=command,
            input_file_pattern=input_file_pattern,
            container_name=container_name,
            vm_size='Standard_D8s_v3',
            unique_id='FoodPort',
        )
        AMRAzureRequest.objects.create(
            amr_request=amr_summary_request, exit_code_file='NA'
        )
    except Exception as e:
        capture_exception(e)
        amr_summary_request.status = 'Error'
        amr_summary_request.save()


@shared_task
def run_mash(tree_request_pk):
    """Submit a Mashtree analysis to Azure Batch.

    Args:
        tree_request_pk: Primary key of the tree request.

    """
    tree_request = Tree.objects.get(pk=tree_request_pk)
    try:
        seqids = tree_request.seqids or []
        container_name = f'tree-{tree_request_pk}'
        run_folder = os.path.join(
            'olc_webportalv2', 'media', f'{container_name}'
        )
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)
        # Select CPU count and VM size from the number of input sequences.
        if len(seqids) < 10:
            vm_size = 'Standard_D4s_v3'
            cpus = 4
        elif len(seqids) < 30:
            vm_size = 'Standard_D8s_v3'
            cpus = 8
        elif len(seqids) < 150:
            vm_size = 'Standard_D16s_v3'
            cpus = 16
        else:
            vm_size = 'Standard_D32s_v3'
            cpus = 32
        cmd = (
            'source $CONDA/activate /envs/mashtree && '
            f'mkdir $AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/'
            f'{container_name} && '
            f'mashtree --numcpus {cpus} '
            f'$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/sequences/*.fasta > '
            f'$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}/'
            f'{container_name}/mash.tree'
        )
        input_file_pattern = []
        for seqid in seqids:
            input_file_pattern.append(
                [f'processed-data/{seqid}.fasta', 'sequences']
            )

        # Submit the command to the AzureBatch service
        generic_api_submit(
            command=cmd,
            container_name=container_name,
            input_file_pattern=input_file_pattern,
            vm_size=vm_size,
            unique_id='FoodPort',
        )

        TreeAzureRequest.objects.create(
            tree_request=tree_request,
            exit_code_file=os.path.join(run_folder, 'exit_codes.txt'),
        )
    except Exception as e:
        capture_exception(e)
        tree_request.status = 'Error'
        tree_request.save()


def send_email(subject, body, recipient):
    """Send an email with the given subject, body, and recipient.

    If an "Access denied" SMTP data error or a "wrong version number" SMTP
    server disconnected error occurs, the function will wait for 5 seconds and
    then retry the operation. This retry process will happen up to 50 times.
    If any other error occurs, it will be raised immediately.

    Args:
        subject (str): The subject of the email.
        body (str): The body of the email.
        recipient (str): The recipient's email address.

    Raises:
        smtplib.SMTPDataError: If an SMTP data error occurs that is not an
        "Access denied" error.
        smtplib.SMTPServerDisconnected: If an SMTP server disconnected error
        occurs that is not a "wrong version number" error.

    """
    # Define the sender's email address
    from_addr = (
        'cfia.foodport.donotreply-nepasrepondre.aliport.acia@inspection.gc.ca'
    )
    # Define the recipient's email address
    to_addr = recipient

    # Create a MIME multipart message
    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    user = os.environ.get('EMAIL_HOST_USER')
    password = os.environ.get('EMAIL_HOST_PASSWORD')
    if not user or not password:
        raise RuntimeError('Email SMTP credentials are not configured.')

    # Attempt to send the email up to 50 times
    for attempt in range(50):
        server = None
        try:
            # Connect to the SMTP server
            server = smtplib.SMTP('email-smtp.ca-central-1.amazonaws.com', 587)
            # Start TLS encryption
            server.starttls()
            # Login to the SMTP server
            server.login(user=user, password=password)
            # Convert the message to a string
            text = msg.as_string()
            # Send the email
            server.sendmail(from_addr, to_addr, text)
            # If the email is sent successfully, break out of the loop
            break
        except smtplib.SMTPDataError as e:
            # If an SMTP data error occurs...
            smtp_error = e.smtp_error
            if isinstance(smtp_error, bytes):
                smtp_error = smtp_error.decode('utf-8', errors='replace')
            if e.smtp_code == 554 and 'Access denied' in smtp_error:
                if attempt == 49:
                    raise
                # If the error is an "Access denied" error, print a message
                # and wait for 5 seconds before retrying
                print('Access denied error occurred, retrying...')
                sleep(5)
            else:
                # If it's a different error, re-raise it
                raise
        except smtplib.SMTPServerDisconnected as e:
            # If the SMTP server gets disconnected...
            if 'wrong version number' in str(e):
                if attempt == 49:
                    raise
                # If the error is a "wrong version number" error, print a
                # message, wait for 5 seconds, and reconnect to the server
                print('Wrong version number error occurred, retrying...')
                sleep(5)
            else:
                # If it's a different error, re-raise it
                raise
        finally:
            # Close the connection to the SMTP server
            if server is not None:
                server.quit()


def _create_benchmark_download(*, benchmark_name, expiry_hours=12):
    """Create a read-only download specification for a benchmark archive.

    Args:
        benchmark_name (str): Canonical or legacy benchmark identifier.
        expiry_hours (int): Number of hours for which the SAS URL is valid.

    Returns:
        dict: Source container, blob name, SAS URL, and content length.

    Raises:
        FileNotFoundError: If the benchmark ZIP does not exist.
        ValueError: If the benchmark identifier is unsupported.

    """
    canonical_name = normalise_benchmark_name(benchmark_name=benchmark_name)
    blob_name = benchmark_blob_name(benchmark_name=canonical_name)

    blob_client = get_storage_client()

    if not blob_client.object_exists(BENCHMARK_CONTAINER_NAME, blob_name):
        raise FileNotFoundError(
            'Benchmark archive does not exist: '
            f'{BENCHMARK_CONTAINER_NAME}/{blob_name}'
        )

    content_length = blob_client.get_object(
        BENCHMARK_CONTAINER_NAME, blob_name
    ).size
    sas_url = blob_client.create_transfer_url(
        BENCHMARK_CONTAINER_NAME,
        'download',
        name=blob_name,
        expires_in_seconds=expiry_hours * 60 * 60,
    )

    return {
        'benchmark_name': canonical_name,
        'container_name': BENCHMARK_CONTAINER_NAME,
        'blob_name': blob_name,
        'sas_url': sas_url,
        'content_length': content_length,
    }


def _create_benchmark_geneseekr_command(*, benchmark_download, container_name):
    """Build the Batch command for a GeneSeekr benchmark analysis.

    The command downloads and extracts the selected benchmark on the Batch
    node's local SSD. It creates a sorted, unique SEQID file from the
    root-level FASTA filenames, then copies that file and the reports
    directory to the
    BlobFuse-mounted request container.

    Args:
        benchmark_download (dict): Result from
            ``_create_benchmark_download``.
        container_name (str): GeneSeekr request container name.

    Returns:
        str: Shell command to submit to Azure Batch.

    """
    local_run = posixpath.join('/datadrive', container_name)
    local_sequences = posixpath.join(local_run, 'sequences')
    local_reports = posixpath.join(local_run, 'reports')
    local_archive = posixpath.join(
        local_run,
        benchmark_download['blob_name'],
    )
    partial_archive = f'{local_archive}.part'

    benchmark_name = benchmark_download['benchmark_name']
    seqids_name = f'{benchmark_name}_seqids.txt'
    local_seqids = posixpath.join(local_run, seqids_name)

    mount_dir = f'$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}'
    target_dir = f'{mount_dir}/targets'
    mounted_reports = f'{mount_dir}/reports'
    mounted_seqids = f'{mount_dir}/{seqids_name}'

    expected_size = str(benchmark_download['content_length'])

    command_parts = [
        'export LC_ALL=C.UTF-8',
        'export LANG=C.UTF-8',
        'source $CONDA/activate /envs/cowbat',
        'set -euo pipefail',
        # Remove files left by an earlier attempt on the same Batch node.
        f'rm -rf {shlex.quote(local_run)}',
        (
            f'mkdir -p {shlex.quote(local_sequences)} '
            f'{shlex.quote(local_reports)}'
        ),
        # Download to a partial path so an interrupted archive is never used.
        (
            'curl --fail --location --retry 5 --retry-delay 10 '
            f'--connect-timeout 60 --continue-at - '
            f'--output {shlex.quote(partial_archive)} '
            f'{shlex.quote(benchmark_download["sas_url"])}'
        ),
        f'test -s {shlex.quote(partial_archive)}',
        # Validate the exact size without nested shell command substitution.
        (
            f'find {shlex.quote(partial_archive)} -maxdepth 0 -type f '
            f'-size {shlex.quote(expected_size)}c -print -quit | grep -q .'
        ),
        f'mv {shlex.quote(partial_archive)} {shlex.quote(local_archive)}',
        f'unzip -tq {shlex.quote(local_archive)}',
        (
            f'unzip -q {shlex.quote(local_archive)} '
            f'-d {shlex.quote(local_sequences)}'
        ),
        f'rm {shlex.quote(local_archive)}',
        # The benchmark contract requires root-level .fasta files.
        (
            f'find {shlex.quote(local_sequences)} -maxdepth 1 -type f '
            "-name '*.fasta' "
            '-print -quit | grep -q .'
        ),
        # Convert root-level FASTA basenames to sorted, unique sequence IDs.
        # The output filename includes the canonical benchmark identifier.
        (
            f'find {shlex.quote(local_sequences)} -maxdepth 1 -type f '
            "-name '*.fasta' "
            f"-printf '%f\\n' | sed 's/\\.fasta$//' | sort -u > "
            f'{shlex.quote(local_seqids)}'
        ),
        f'test -s {shlex.quote(local_seqids)}',
        (
            f'GeneSeekr blastn -u -s {shlex.quote(local_sequences)} '
            f'-t {target_dir} -r {shlex.quote(local_reports)}'
        ),
        # Do not report success unless GeneSeekr created at least one result.
        (
            f'find {shlex.quote(local_reports)} -type f '
            '-print -quit | grep -q .'
        ),
        # Replace only persistent outputs belonging to this analysis.
        f'rm -rf {mounted_reports}',
        f'rm -f {mounted_seqids}',
        f'cp -R {shlex.quote(local_reports)} {mount_dir}/',
        f'cp {shlex.quote(local_seqids)} {mount_dir}/',
    ]

    return ' && '.join(command_parts)


@shared_task
def run_geneseekr(geneseekr_request_pk):
    """Prepare and submit a GeneSeekr request to Azure Batch.

    Benchmark requests download their selected archive directly from the
    shared ``benchmark-datasets`` container using a read-only SAS URL. Normal
    requests retain the existing processed-data staging workflow.

    Args:
        geneseekr_request_pk (int): Primary key of the GeneSeekr request.

    """
    geneseekr_request = GeneSeekrRequest.objects.get(pk=geneseekr_request_pk)

    try:
        container_name = f'geneseekr-{geneseekr_request_pk}'
        run_folder = os.path.join(
            'olc_webportalv2',
            'media',
            container_name,
        )
        if not os.path.isdir(run_folder):
            os.makedirs(run_folder)

        file_name = 'query.fasta'
        target_file = os.path.join(run_folder, file_name)
        with open(target_file, 'w', encoding='utf-8') as target_object:
            target_object.write(geneseekr_request.query_sequence)

        blob_client = get_storage_client()
        blob_client.ensure_container(container_name)
        blob_client.upload_file(
            container_name,
            posixpath.join('targets', file_name),
            target_file,
        )

        sequence_count = len(geneseekr_request.seqids)
        if sequence_count < 10:
            vm_size = 'Standard_D4s_v3'
        elif sequence_count < 30:
            vm_size = 'Standard_D8s_v3'
        elif sequence_count < 150:
            vm_size = 'Standard_D16s_v3'
        else:
            vm_size = 'Standard_D32s_v3'

        input_file_pattern = []

        if geneseekr_request.benchmark:
            vm_size = 'Standard_D32s_v3'
            benchmark_download = _create_benchmark_download(
                benchmark_name=geneseekr_request.benchmark
            )
            cmd = _create_benchmark_geneseekr_command(
                benchmark_download=benchmark_download,
                container_name=container_name,
            )
        else:
            path = f'$AZ_BATCH_NODE_MOUNTS_DIR/{container_name}'
            cmd = (
                'export LC_ALL=C.UTF-8 && export LANG=C.UTF-8 && '
                'source $CONDA/activate /envs/cowbat && '
            )

            if sequence_count > 50:
                blob_file = zip_files(
                    seqids=geneseekr_request.seqids,
                    target_folder=container_name,
                    container_name='processed-data',
                )
                input_file_pattern.append(f'temporary-storage/{blob_file}')
                cmd += (
                    f'mkdir -p {path}/sequences && '
                    f'unzip {path}/{blob_file} -d {path}/sequences && '
                    f'rm {path}/{blob_file} && '
                )
            else:
                for seqid in geneseekr_request.seqids:
                    input_file_pattern.append(
                        f'processed-data/{seqid}.fasta sequences'
                    )

            cmd += (
                f'GeneSeekr blastn -u -s {path}/sequences '
                f'-t {path}/targets -r {path}/reports'
            )

        generic_api_submit(
            command=cmd,
            container_name=container_name,
            input_file_pattern=input_file_pattern,
            vm_size=vm_size,
            unique_id='FoodPort',
        )
        GeneSeekrAzureRequest.objects.create(
            geneseekr_request=geneseekr_request,
            exit_code_file='NA',
        )
    except Exception as exc:
        capture_exception(exc)
        geneseekr_request.status = 'Error'
        geneseekr_request.error = str(exc)
        geneseekr_request.save()


#################### NEAREST NEIGHBORS TASK ##############################
@shared_task
def run_nearest_neighbors(nearest_neighbor_pk):
    """Run Mash distance analysis and save the closest matching sequences.

    Args:
        nearest_neighbor_pk: Primary key of the nearest-neighbor request.

    """
    nearest_neighbor_request = NearestNeighbors.objects.get(
        pk=nearest_neighbor_pk
    )
    try:
        work_dir = f'olc_webportalv2/media/neighbor-{nearest_neighbor_pk}'
        if not os.path.isdir(work_dir):
            os.makedirs(work_dir)
        seqids_in_metadata = list()
        for sequence_data in SequenceData.objects.filter():
            seqids_in_metadata.append(sequence_data.seqid)
        # Download the requested sequence from blob storage.
        blob_client = get_storage_client()
        seqid = nearest_neighbor_request.seqid or ''
        uploaded_file_name = nearest_neighbor_request.uploaded_file_name or ''
        if seqid:
            fasta_file = os.path.join(work_dir, f'{seqid}.fasta')
            blob_client.download_file(
                'processed-data',
                f'{seqid}.fasta',
                fasta_file,
            )
        elif uploaded_file_name:
            fasta_file = os.path.join(work_dir, uploaded_file_name)
            blob_client.download_file(
                f'neighbor-{nearest_neighbor_request.pk}',
                uploaded_file_name,
                fasta_file,
            )
        else:
            raise ValueError(
                'Nearest-neighbor request has neither a SeqID nor an '
                'uploaded file.'
            )

        mash_output_file = os.path.join(work_dir, 'mash_dist_results.tsv')
        with open(mash_output_file, 'w', encoding='utf-8') as output_file:
            subprocess.run(
                [
                    '/data/web/mash-Linux64-v2.1/mash',
                    'dist',
                    fasta_file,
                    '/data/web/sketchomatic.msh',
                ],
                stdout=output_file,
                check=True,
            )
        shutil.make_archive(work_dir, 'zip', work_dir)
        sas_url = generate_download_link(
            blob_service_client=blob_client,
            container_name=f'neighbor-output-{nearest_neighbor_pk}',
            output_zipfile=work_dir + '.zip',
            expiry=8,
        )
        nearest_neighbor_request.download_link = sas_url
        distances = dict()
        with open(mash_output_file, encoding='utf-8') as result_file:
            for line in result_file:
                x = line.split()
                query_seqid = x[1].split('/')[-1].replace('.fasta', '')
                query_distance = float(x[2])
                # Don't show the fact we get match to self. Not useful info
                if query_seqid != seqid and query_seqid in seqids_in_metadata:
                    distances[query_seqid] = query_distance
        sorted_distances = sorted(distances.items(), key=lambda kv: kv[1])
        for neighbor_seqid, distance in sorted_distances[
            : nearest_neighbor_request.number_neighbors
        ]:
            NearNeighborDetail.objects.create(
                near_neighbor_request=nearest_neighbor_request,
                seqid=neighbor_seqid,
                distance=distance,
            )
        shutil.rmtree(work_dir)
        os.remove(work_dir + '.zip')
        nearest_neighbor_request.status = 'Complete'
        nearest_neighbor_request.save()
    except Exception as e:
        capture_exception(e)
        nearest_neighbor_request.status = 'Error'
        nearest_neighbor_request.save()
