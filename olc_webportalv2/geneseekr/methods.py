"""General methods for the GeneSeekr app."""

# Standard imports
import os
import shutil

from olc_webportalv2.common.storage_client import get_storage_client


def zip_files(
    seqids: list[str], target_folder: str, container_name: str
) -> str:
    """Download matching sequence files, archive them, and upload the archive.

    Args:
        seqids: Sequence identifiers whose FASTA files should be included.
        target_folder: Local folder name and uploaded archive name prefix.
        container_name: Source blob container containing the sequence files.

    Returns:
        Name of the uploaded archive blob.

    """
    # Set the destination folder for the downloaded files
    job_dir = os.path.join('olc_webportalv2', 'media', target_folder, 'tmp')
    # Create the folder if required
    os.makedirs(job_dir, exist_ok=True)
    blob_client = get_storage_client()
    # Create a generator of all the blobs in the container
    blobs = blob_client.list_objects(container_name)
    # Create a list of all the blob names
    blob_names = [blob.name for blob in blobs]
    for blob_name in blob_names:
        for seqid in seqids:
            # Ensure that we're looking at the correct blob file
            if seqid in blob_name:
                # Download the file
                blob_client.download_file(
                    container_name,
                    blob_name,
                    os.path.join(job_dir, os.path.basename(blob_name)),
                )
                continue
    # Create an archive of the downloaded files
    shutil.make_archive(job_dir, 'zip', job_dir)
    # Set the name of the archive
    archive = job_dir + '.zip'
    # Set the name of the target container
    target_container = 'temporary-storage'
    # Create the target container if necessary
    blob_client.ensure_container(target_container)
    # Set the name of the blob file
    blob_file = target_folder + '.zip'
    # Upload the archive to the destination container
    blob_client.upload_file(target_container, blob_file, archive)
    # Remove the archive
    os.remove(archive)
    # Remove the temporary folder
    shutil.rmtree(job_dir)
    return blob_file
