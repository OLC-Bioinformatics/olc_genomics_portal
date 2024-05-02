"""
General methods for the GeneSeekr app
"""

# Standard imports
import os
import shutil

# Django imports
from django.conf import settings

# Azure imports
from azure.storage.blob import BlockBlobService


def zip_files(
    seqids: list,
    target_folder: str,
    container_name: str):
    """
    Create a local archive from a list of SEQIDs. Upload the archive to a target container
    """
    # Set the destination folder for the downloaded files
    job_dir = os.path.join('olc_webportalv2', 'media',  target_folder, 'tmp')
    # Create the folder if required
    os.makedirs(job_dir, exist_ok=True)
    # Create a blob client to allow manipulation of files in blob storage
    blob_client = BlockBlobService(
        account_key=settings.AZURE_ACCOUNT_KEY,
        account_name=settings.AZURE_ACCOUNT_NAME
    )
    # Create a generator of all the blobs in the container
    blobs = blob_client.list_blobs(container_name=container_name)
    # Create a list of all the blob names
    blob_names = [blob.name for blob in blobs]
    for blob_name in blob_names:
        for seqid in seqids:
            # Ensure that we're looking at the correct blob file
            if seqid in blob_name:
                # Download the file
                blob_client.get_blob_to_path(
                    container_name=container_name,
                    blob_name=blob_name,
                    file_path=os.path.join(job_dir, os.path.basename(blob_name))
                )
                continue
    # Create an archive of the downloaded files
    shutil.make_archive(job_dir, 'zip', job_dir)
    # Set the name of the archive
    archive = job_dir + '.zip'
    # Set the name of the target container
    target_container = 'temporary-storage'
    # Create the target container if necessary
    blob_client.create_container(target_container)
    # Set the name of the blob file
    blob_file = target_folder + '.zip'
    # Upload the archive to the destination container
    blob_client.create_blob_from_path(
        container_name=target_container,
        blob_name=blob_file,
        file_path=archive
    )
    # Remove the archive
    os.remove(archive)
    # Remove the temporary folder
    shutil.rmtree(job_dir)
    return blob_file
