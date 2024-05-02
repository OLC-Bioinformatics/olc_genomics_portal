#!/usr/bin/env python
# Django imports
from django.conf import settings

# Standard imports
import datetime
import shutil
import os

# Third-party imports
import azure.batch.batch_service_client as batch
from azure.storage.blob import BlockBlobService
from azure.storage.blob import BlobPermissions
import azure.batch.batch_auth as batch_auth
import azure.batch.models as batchmodels

from celery import shared_task

# Portal-specific imports
from olc_webportalv2.primer_finder.methods import \
    upload_primers, \
    upload_probe, \
    create_details, \
    format_batch_config, \
    populate_report, \
    populate_sequence_details, \
    send_email
from olc_webportalv2.primer_finder.models import \
    PrimerVerifierRequest, \
    VerifierPrimerSet, \
    VerifierPanel, \
    VerifierSEQID, \
    VerifierAzureRequest, \
    ValidatorRequest, \
    ValidatorPrimerSet, \
    ValidatorPanel, \
    ValidatorSEQID, \
    PrimerValidatorAzureRequest
from olc_webportalv2.cowbat.methods import AzureBatch


@shared_task
def run_primer_verifier(verifier_request_pk):
    # Retrieve the PrimerVerifierRequests object corresponding to the verifier_request primary key
    verifier_request = PrimerVerifierRequest.objects.get(pk=verifier_request_pk)
    try:
        container_name, run_folder, file_name, input_container = upload_primers(request=verifier_request)
        # Set the name and path of the batch config file
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # Create the PrimerValidator system call using the supplied arguments
        command = 'source $CONDA/activate /envs/primer_validator && ' \
                  'primer_validator.py ' \
                  '-i {inclusivity_sequence_dir} ' \
                  '-e {exclusivity_sequence_dir} ' \
                  '-pf {primer_file_location} ' \
                  '-r {report_dir} ' \
                  '-m {mismatches} ' \
                  '-min {min_amplicon_size} ' \
                  '-max {max_amplicon_size} ' \
                  '-rb {range_buffer} ' \
            .format(
                inclusivity_sequence_dir='inclusivity',
                exclusivity_sequence_dir='exclusivity',
                primer_file_location=os.path.join('primers', file_name),
                report_dir='reports',
                mismatches=verifier_request.mismatches,
                min_amplicon_size=verifier_request.min_amplicon_size,
                max_amplicon_size=verifier_request.max_amplicon_size,
                range_buffer=verifier_request.range_buffer
            )
        if verifier_request.contig_breaks:
            command += '-cb'
        # Create the string to be written to the batch config file
        batch_string = format_batch_config(
            job_name=container_name,
            vm_size='Standard_D2s_v3'
        )
        # All the sequences are stored in 'primer-verifier-$genus
        verifier_container = 'primer-verifier'
        # Run queries to retrieve all objects corresponding to the appropriate panel
        inclusivity_panel = VerifierPanel.objects.filter(verifier_request_id=verifier_request_pk, panel='inclusivity')
        exclusivity_panel = VerifierPanel.objects.filter(verifier_request_id=verifier_request_pk, panel='exclusivity')

        # Cloud input files
        # If the query retrieved objects corresponding to the inclusivity panel
        if inclusivity_panel:
            # Iterate through all the genera in the panel
            for panel in inclusivity_panel:
                # Update the batch string with the container location and the folder on the batch VM into which the
                # files are to be written
                batch_string += 'CLOUDIN:={container}-{genus}/*.fasta inclusivity\n'.format(
                    container=verifier_container,
                    genus=panel.genus.lower())
        # If no inclusivity genera are specified, create a folder with an empty text file, so that the script doesn't
        # crash when the necessary inclusivity folder is missing
        else:
            batch_string += 'CLOUDIN:={container}-empty/*.txt inclusivity\n'.format(container=verifier_container)
        # Ensure that the exclusivity query returned results
        if exclusivity_panel:
            # Iterate through all general in the panel
            for panel in exclusivity_panel:
                # Update the batch string
                batch_string += 'CLOUDIN:={container}-{genus}/*.fasta exclusivity\n'.format(
                    container=verifier_container,
                    genus=panel.genus.lower())
        # If no exclusivity genera are specified, create a folder with an empty text file, so that the script doesn't
        # crash when the necessary exclusivity folder is missing
        else:
            batch_string += 'CLOUDIN:={container}-empty/*.txt exclusivity\n'.format(container=verifier_container)
        # Update the batch string with the primer file
        batch_string += 'CLOUDIN:={container}/{file_name} primers\n' \
            .format(
                container=input_container,
                file_name=file_name
            )
        # Cloud output files
        # Specify that the reports folder will be uploaded to the output-container (primer-verifier-$pk-output)
        batch_string += 'OUTPUT:=reports/\n'
        # Update the batch string with the PrimerValidator system call
        batch_string += 'COMMAND:={command}\n'.format(command=command)
        # Write the batch string to file
        with open(batch_config_file, 'w') as batch_config:
            batch_config.write(batch_string)
        # Create an instance of AzureBatch
        azure_task = AzureBatch()
        # Submit the Azure batch request using the AzureBatch class
        azure_task.main(
            configuration_file=batch_config_file,
            job_name=container_name,
            output_dir=run_folder,
            settings=settings,
            keep_input_container=True,
            download_output_files=True,
            vm_size='Standard_D2s_v3',
            no_clean=True,
        )
        # Create a VerifierAzureRequest for this PrimerVerifierRequest
        VerifierAzureRequest.objects.create(
            verifier_request=verifier_request,
            exit_code_file='NA'
        )
    # If something goes wrong, capture and save the error
    except Exception as e:
        verifier_request.status = 'Error'
        verifier_request.errors = e
        verifier_request.save()


def check_verifier_tasks():
    """
    Check the status of tasks. If task fails, perform clean-up. If task succeeds, perform necessary steps and clean-up
    """
    # Retrieve all VerifierAzureRequest objects (they should be deleted after they finish, so anything retrieved
    # should be active)
    verifier_tasks = VerifierAzureRequest.objects.filter()
    # Extract the credentials from the settings
    credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
    # Create the batch client for manipulating batch jobs, and pools
    batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
    for task in verifier_tasks:
        # Retrieve the PrimerVerifierRequests object corresponding to the verifier_request primary key
        verifier_request = PrimerVerifierRequest.objects.get(pk=task.verifier_request.pk)
        # Set the container name appropriately
        batch_job_name = verifier_request.container_namer()
        # Check if tasks related with this PrimerVerifier job have finished.
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        # If something errors first time through, jobs can't get deleted. In that case, give up.
        except Exception as e:
            PrimerVerifierRequest.objects.filter(pk=task.verifier_request.pk).update(status='Error', errors=e)
            # Delete task, so we don't keep iterating over it.
            VerifierAzureRequest.objects.filter(id=task.id).delete()
            continue
        # If tasks have completed, check if they were successful.
        if tasks_completed:
            # Initialise the exit code status to True
            exit_codes_good = True
            # Iterate through the tasks associated with the name of the batch job
            for cloudtask in batch_client.task.list(batch_job_name):
                # The only 'good' exit code is 0
                if cloudtask.execution_info.exit_code != 0:
                    # A non-zero code sets the boolean to False
                    exit_codes_good = False
            # Get rid of job and pool, so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)
            # If the task exited successfully, do some clean-up, populate models, etc.
            if exit_codes_good:
                # Set the name of the output container and run folder
                output_container = batch_job_name + '-output'
                run_folder = os.path.join(
                    'olc_webportalv2', 'media', '{container_name}'.format(container_name=batch_job_name))
                # Create the blob service client for manipulating blobs
                blob_client = BlockBlobService(
                    account_name=settings.AZURE_ACCOUNT_NAME,
                    account_key=settings.AZURE_ACCOUNT_KEY
                )
                # Remove the container containing the primer file
                try:
                    blob_client.delete_container(container_name=batch_job_name + '-input')
                except:
                    pass
                # Download the blob container, and store the JSON-formatted report into the model
                verifier_request = populate_report(request=verifier_request)
                # Parse the JSON-formatted report, and populate the sequence details from the report
                validator_request = populate_sequence_details(
                    request=verifier_request,
                    primer_set_model=VerifierPrimerSet,
                    panel_model=VerifierPanel,
                    seqid_model=VerifierSEQID
                )
                # Create a JSON-formatted summary report
                verifier_request = create_details(
                    request=verifier_request,
                    primer_set_model=VerifierPrimerSet,
                    panel_model=VerifierPanel,
                    seqid_model=VerifierSEQID
                )
                # Generate an SAS url with read access that users will be able to use to download their reports.
                sas_token = blob_client.generate_container_shared_access_signature(
                    container_name=output_container,
                    permission=BlobPermissions.READ,
                    expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8)
                )
                # Create SAS URLs for both the PrimerVerifier report, and the summary report
                report_sas_url = blob_client.make_blob_url(
                    container_name=output_container,
                    blob_name='reports/inclusivity_exclusivity_report.json',
                    sas_token=sas_token
                )
                summary_sas_url = blob_client.make_blob_url(
                    container_name=output_container,
                    blob_name='reports/{name}_summary_report.json'.format(name=verifier_request.container_namer()),
                    sas_token=sas_token
                )
                # Update the model with the SAS URLs
                verifier_request.report_download_link = report_sas_url
                verifier_request.summary_download_link = summary_sas_url
                verifier_request.status = 'Complete'
                verifier_request.save()
                # Send emails
                email_list = verifier_request.emails_array
                for email in email_list:
                    send_email(
                        subject='PrimerVerifier Analysis "{name}" Complete'
                                .format(name=str(verifier_request.project_name)),
                        body='Dear {user},\n'
                             'Your PrimerVerifier analysis, "{name}", is complete.\n'
                             'Raw PrimerVerifier outputs are available here: {report_url}.\n\n'
                             'Summarised outputs are available here: {summary_url}.\n\n'
                             'Best regards,\n'
                             'The FoodPort development team'
                             .format(user=verifier_request.user,
                                     name=str(verifier_request.project_name),
                                     report_url=report_sas_url,
                                     summary_url=summary_sas_url),
                        recipient=email)
                # Finally, do some cleanup - delete the reports folder if the JSON report was loaded into the model
                if verifier_request.report:
                    shutil.rmtree(run_folder)
            else:
                # Send emails
                email_list = verifier_request.emails_array
                for email in email_list:
                    send_email(
                        subject='PrimerVerifier Analysis "{name}" Failed'
                                .format(name=str(verifier_request.project_name)),
                        body='Dear {user},\n'
                             'Your PrimerVerifier analysis, "{name}", has failed.\n'
                             'Sorry for the inconvenience,\n'
                             'The FoodPort development team'
                             .format(
                                 user=verifier_request.user,
                                 name=str(verifier_request.project_name)
                                ),
                        recipient=email)
                # Update the model with the error status
                verifier_request.status = 'Error'
                verifier_request.save()
            # Delete the VerifierAzureRequest
            VerifierAzureRequest.objects.filter(id=task.id).delete()


@shared_task
def run_primer_validator(validator_request_pk):
    # Retrieve the PrimerValidatorRequests object corresponding to the validator_request primary key
    validator_request = ValidatorRequest.objects.get(pk=validator_request_pk)
    try:
        container_name, run_folder, file_name, input_container = upload_primers(request=validator_request)
        upload_probe(request=validator_request)
        # Set the name and path of the batch config file
        batch_config_file = os.path.join(run_folder, 'batch_config.txt')
        # Create the PrimerValidator system call using the supplied arguments
        command = 'source $CONDA/activate /envs/primer_validator && ' \
                  'primer_validator.py ' \
                  '-i {inclusivity_sequence_dir} ' \
                  '-e {exclusivity_sequence_dir} ' \
                  '-pf {primer_file_location} ' \
                  '-r {report_dir} ' \
                  '-m 3' \
            .format(
                inclusivity_sequence_dir='inclusivity',
                exclusivity_sequence_dir='exclusivity',
                primer_file_location=os.path.join('primers', file_name),
                report_dir='reports',
                )
        if validator_request.probe_sequence:
            command += ' -p {probe}'.format(probe=os.path.join('probe', 'probe.fasta'))
        # Create the string to be written to the batch config file
        batch_string = format_batch_config(
            job_name=container_name,
            vm_size='Standard_D2s_v3'
        )
        # All the sequences are stored in 'primer-validator-$genus
        validator_container = 'primer-validator'
        # Run queries to retrieve all objects corresponding to the appropriate panel
        inclusivity_panel = ValidatorPanel.objects.filter(
            validator_request_id=validator_request_pk,
            panel='inclusivity'
        )
        exclusivity_panel = ValidatorPanel.objects.filter(
            validator_request_id=validator_request_pk,
            panel='exclusivity'
        )

        # Cloud input files
        # Iterate through all the genera in the panel
        for panel in inclusivity_panel:
            # Update the batch string with the container location and the folder on the batch VM into which the
            # files are to be written
            batch_string += 'CLOUDIN:={container}-{genus}/*.fasta inclusivity\n'.format(
                container=validator_container,
                genus=panel.genus.lower()
            )
        # Iterate through all general in the panel
        for panel in exclusivity_panel:
            # Update the batch string
            batch_string += 'CLOUDIN:={container}-{genus}/*.fasta exclusivity\n'.format(
                container=validator_container,
                genus=panel.genus.lower()
            )
        # Update the batch string with the primer file
        batch_string += 'CLOUDIN:={container}/{file_name} primers\n' \
            .format(
                container=input_container,
                file_name=file_name
            )
        if validator_request.probe_sequence:
            # Update the batch string with the probe file
            batch_string += 'CLOUDIN:={container}/{file_name} probe\n' \
                .format(
                    container=input_container,
                    file_name='probe.fasta'
                )
        # Cloud output files
        # Specify that the reports folder will be uploaded to the output-container (primer-validator-$pk-output)
        batch_string += 'OUTPUT:=reports/\n'
        # Update the batch string with the PrimerValidator system call
        batch_string += 'COMMAND:={command}\n'.format(command=command)
        # Write the batch string to file
        with open(batch_config_file, 'w') as batch_config:
            batch_config.write(batch_string)
        # Create an instance of AzureBatch
        azure_task = AzureBatch()
        # Submit the Azure batch request using the AzureBatch class
        azure_task.main(
            configuration_file=batch_config_file,
            job_name=container_name,
            output_dir=run_folder,
            settings=settings,
            keep_input_container=True,
            download_output_files=True,
            vm_size='Standard_D2s_v3',
            no_clean=True,
        )
        # Create a ValidatorAzureRequest for this PrimerValidatorRequest
        PrimerValidatorAzureRequest.objects.create(
            validator_request=validator_request,
            exit_code_file='NA'
        )
    # If something goes wrong, capture and save the error
    except Exception as e:
        validator_request.status = 'Error'
        validator_request.errors = e
        validator_request.save()


def check_validator_tasks():
    """
    Check the status of tasks. If task fails, perform clean-up. If task succeeds, perform necessary steps and clean-up
    """
    # Retrieve all ValidatorAzureRequest objects (they should be deleted after they finish, so anything retrieved
    # should be active)
    validator_tasks = PrimerValidatorAzureRequest.objects.filter()
    # Extract the credentials from the settings
    credentials = batch_auth.SharedKeyCredentials(settings.BATCH_ACCOUNT_NAME, settings.BATCH_ACCOUNT_KEY)
    # Create the batch client for manipulating batch jobs, and pools
    batch_client = batch.BatchServiceClient(credentials, base_url=settings.BATCH_ACCOUNT_URL)
    for task in validator_tasks:
        # Retrieve the PrimerValidatorRequests object corresponding to the validator_request primary key
        validator_request = ValidatorRequest.objects.get(pk=task.validator_request.pk)
        # Set the container name appropriately
        batch_job_name = validator_request.container_namer()
        # Check if tasks related with this PrimerValidator job have finished.
        tasks_completed = True
        try:
            for cloudtask in batch_client.task.list(batch_job_name):
                if cloudtask.state != batchmodels.TaskState.completed:
                    tasks_completed = False
        # If something errors first time through, jobs can't get deleted. In that case, give up.
        except Exception as e:
            ValidatorRequest.objects.filter(pk=task.validator_request.pk).update(status='Error', errors=e)
            # Delete task, so we don't keep iterating over it.
            PrimerValidatorAzureRequest.objects.filter(id=task.id).delete()
            continue
        # If tasks have completed, check if they were successful.
        if tasks_completed:
            # Initialise the exit code status to True
            exit_codes_good = True
            # Iterate through the tasks associated with the name of the batch job
            for cloudtask in batch_client.task.list(batch_job_name):
                # The only 'good' exit code is 0
                if cloudtask.execution_info.exit_code != 0:
                    # A non-zero code sets the boolean to False
                    exit_codes_good = False
            # Get rid of job and pool, so we don't waste big $$$ and do cleanup/get files downloaded in tasks.
            batch_client.job.delete(job_id=batch_job_name)
            batch_client.pool.delete(pool_id=batch_job_name)
            # If the task exited successfully, do some clean-up, populate models, etc.
            if exit_codes_good:
                # Set the name of the output container and run folder
                output_container = batch_job_name + '-output'
                run_folder = os.path.join('olc_webportalv2', 'media', '{container_name}'
                                          .format(container_name=batch_job_name))
                # Create the blob service client for manipulating blobs
                blob_client = BlockBlobService(
                    account_name=settings.AZURE_ACCOUNT_NAME,
                    account_key=settings.AZURE_ACCOUNT_KEY
                )
                # Remove the container containing the primer file
                try:
                    blob_client.delete_container(container_name=batch_job_name + '-input')
                except:
                    pass
                # Download the blob container, and store the JSON-formatted report into the model
                validator_request = populate_report(request=validator_request)
                # Parse the JSON-formatted report, and populate the sequence details from the report
                validator_request = populate_sequence_details(
                    request=validator_request,
                    primer_set_model=ValidatorPrimerSet,
                    panel_model=ValidatorPanel,
                    seqid_model=ValidatorSEQID,
                    analysis='validator'
                )
                # Create a JSON-formatted summary report
                validator_request = create_details(
                    request=validator_request,
                    primer_set_model=ValidatorPrimerSet,
                    panel_model=ValidatorPanel,
                    seqid_model=ValidatorSEQID,
                    analysis='validator'
                )
                # Generate an SAS url with read access that users will be able to use to download their reports.
                sas_token = blob_client.generate_container_shared_access_signature(
                    container_name=output_container,
                    permission=BlobPermissions.READ,
                    expiry=datetime.datetime.utcnow() + datetime.timedelta(days=8)
                )
                # Create SAS URLs for both the PrimerValidator report, and the summary report
                report_sas_url = blob_client.make_blob_url(
                    container_name=output_container,
                    blob_name='reports/inclusivity_exclusivity_report.json',
                    sas_token=sas_token
                )
                summary_sas_url = blob_client.make_blob_url(
                    container_name=output_container,
                    blob_name='reports/{name}_summary_report.json'.format(name=validator_request.container_namer()),
                    sas_token=sas_token
                )
                # Update the model with the SAS URLs
                validator_request.report_download_link = report_sas_url
                validator_request.summary_download_link = summary_sas_url
                validator_request.status = 'Complete'
                validator_request.save()
                # Send emails
                email_list = validator_request.emails_array
                for email in email_list:
                    send_email(
                        subject='PrimerValidator Analysis "{name}" Complete'
                                .format(name=str(validator_request.project_name)),
                        body='Dear {user},\n'
                             'Your PrimerValidator analysis, "{name}", is complete.\n'
                             'Raw PrimerValidator outputs are available here: {report_url}.\n\n'
                             'Summarised outputs are available here: {summary_url}.\n\n'
                             'Best regards,\n'
                             'The FoodPort development team'
                             .format(
                                    user=validator_request.user,
                                    name=str(validator_request.project_name),
                                    report_url=report_sas_url,
                                    summary_url=summary_sas_url),
                        recipient=email
                    )
                # Finally, do some cleanup - delete the reports folder if the JSON report was loaded into the model
                if validator_request.report:
                    shutil.rmtree(run_folder)
            else:
                # Send emails
                email_list = validator_request.emails_array
                for email in email_list:
                    send_email(
                        subject='PrimerValidator Analysis "{name}" Failed'
                                .format(name=str(validator_request.project_name)),
                        body='Dear {user},\n'
                             'Your PrimerValidator analysis, "{name}", has failed.\n'
                             'Sorry for the inconvenience,\n'
                             'The FoodPort development team'
                             .format(
                                    user=validator_request.user,
                                    name=str(validator_request.project_name)),
                        recipient=email
                    )
                # Update the model with the error status
                validator_request.status = 'Error'
                validator_request.save()
            # Delete the ValidatorAzureRequest
            PrimerValidatorAzureRequest.objects.filter(id=task.id).delete()
