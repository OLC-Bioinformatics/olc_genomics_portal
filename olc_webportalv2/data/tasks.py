"""Celery tasks for packaging raw and processed sequence data."""

import logging
import os
import re
import shutil

from celery import shared_task
from sentry_sdk import capture_exception

from olc_webportalv2.common.storage_client import get_storage_client
from olc_webportalv2.data.models import DataRequest

logger = logging.getLogger(__name__)


@shared_task
def get_assembled_data(data_request_pk: int) -> None:
    """Package processed FASTA files for a saved request.

    Args:
        data_request_pk: Primary key of the data request to process.

    """
    data_request = DataRequest.objects.get(pk=data_request_pk)
    try:
        data_dir = f'olc_webportalv2/media/data_request_{data_request_pk}'
        if not os.path.isdir(data_dir):
            os.makedirs(data_dir)
        container_name = 'processed-data'
        logger.info('Connecting to storage for request %s', data_request_pk)
        blob_client = get_storage_client()
        blob_files = blob_client.list_objects(container_name)
        blob_filename_list = [
            blob.name[:-6]
            for blob in blob_files
            if blob.name.endswith('.fasta')
        ]
        for seqid in data_request.seqids:
            if seqid in blob_filename_list:
                blob_client.download_file(
                    container_name,
                    seqid + '.fasta',
                    os.path.join(data_dir, seqid + '.fasta'),
                )
            else:
                data_request.missing_seqids.append(seqid)
        logger.info('Creating FASTA archive for request %s', data_request_pk)
        shutil.make_archive(data_dir, 'zip', data_dir)
        logger.info('Uploading FASTA archive for request %s', data_request_pk)
        data_request_container = f'data-request-{data_request_pk}'
        blob_client.ensure_container(data_request_container)
        blob_name = os.path.split(data_dir + '.zip')[1]
        blob_client.upload_file(
            data_request_container, blob_name, data_dir + '.zip'
        )
        sas_url = blob_client.create_transfer_url(
            data_request_container,
            'download',
            name=blob_name,
            expires_in_seconds=8 * 24 * 60 * 60,
        )
        data_request.download_link = sas_url

        logger.info('Cleaning up files for request %s', data_request_pk)
        shutil.rmtree(data_dir)
        os.remove(data_dir + '.zip')
        if len(data_request.missing_seqids) == 0:
            data_request.status = 'Complete'
        else:
            data_request.status = 'Warning'
        data_request.save()
    except Exception as e:
        capture_exception(e)
        logger.exception('Failed to process FASTA request %s', data_request_pk)
        data_request.status = 'Error'
        data_request.save(update_fields=['status'])


@shared_task
def get_raw_data(data_request_pk: int) -> None:
    """Package raw FASTQ files for a saved request.

    Args:
        data_request_pk: Primary key of the data request to process.

    """
    data_request = DataRequest.objects.get(pk=data_request_pk)
    data_dir = f'olc_webportalv2/media/data_request_{data_request_pk}'
    try:
        logger.info('Processing FASTQ request %s', data_request_pk)
        if not os.path.isdir(data_dir):
            os.makedirs(data_dir)
        blob_client = get_storage_client()
        containers = blob_client.list_containers()
        fastq_dict = {}
        file_hits = set()
        missing_seqids = []
        for container in containers:
            if (
                re.match(r'\d{6}-[a-z0-9]', container.name)
                and '-output' not in container.name
            ):
                blob_files = blob_client.list_objects(container.name)
                for blob_file in blob_files:
                    match = re.search(
                        r'(\d{4}-[A-Z0-9]{3,5}-\d{4}).+\.fastq\.gz$',
                        blob_file.name,
                    )
                    if match:
                        base_seqid = match.group(1)
                        if base_seqid in data_request.seqids:
                            if base_seqid not in fastq_dict:
                                fastq_dict[base_seqid] = {}
                            if blob_file.name not in file_hits:
                                file_hits.add(blob_file.name)
                                if (
                                    container.name
                                    not in fastq_dict[base_seqid]
                                ):
                                    fastq_dict[base_seqid][container.name] = []
                                fastq_dict[base_seqid][container.name].append(
                                    blob_file.name
                                )
        for seqid in data_request.seqids:
            try:
                fastq_files = fastq_dict[seqid]
                for container_name, fastqs_list in fastq_files.items():
                    for fastq in fastqs_list:
                        blob_client.download_file(
                            container_name,
                            fastq,
                            os.path.join(data_dir, fastq),
                        )
            except KeyError:
                missing_seqids.append(seqid)
        logger.info('Creating FASTQ archive for request %s', data_request_pk)
        shutil.make_archive(data_dir, 'zip', data_dir)
        logger.info('Uploading FASTQ archive for request %s', data_request_pk)
        data_request_container = f'data-request-{data_request_pk}'
        blob_client.ensure_container(data_request_container)
        blob_name = os.path.split(data_dir + '.zip')[1]
        blob_client.upload_file(
            data_request_container, blob_name, data_dir + '.zip'
        )
        sas_url = blob_client.create_transfer_url(
            data_request_container,
            'download',
            name=blob_name,
            expires_in_seconds=8 * 24 * 60 * 60,
        )
        data_request.download_link = sas_url

        shutil.rmtree(data_dir)
        os.remove(data_dir + '.zip')
        data_request.missing_seqids = missing_seqids
        if not missing_seqids:
            data_request.status = 'Complete'
        else:
            data_request.status = 'Warning'
        data_request.save()
    except Exception as e:
        capture_exception(e)
        logger.exception('Failed to process FASTQ request %s', data_request_pk)
        data_request.status = 'Error'
        data_request.save(update_fields=['status'])
        for path, cleanup in (
            (data_dir, shutil.rmtree),
            (f'{data_dir}.zip', os.remove),
        ):
            try:
                cleanup(path)
            except FileNotFoundError:
                pass
            except OSError:
                logger.exception(
                    'Could not clean up data request path %s', path
                )
