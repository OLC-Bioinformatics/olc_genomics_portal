from azure.common.credentials import ServicePrincipalCredentials
from azure.storage.blob import BlockBlobService
import azure.batch.batch_auth as batch_auth
import azure.batch.batch_service_client as batch
from azure.storage.blob import BlobPermissions
import azure.batch.models as batchmodels
from time import time
import datetime
import fnmatch
import logging
import msrest
import random
import string
import glob
import os


class BatchClass:
    """
    Class for doing azure batch operations to make job submission relatively easy.
    The parse_configuration_file function needs to be run to create one of these objects and set
    attributes, or things really won't work very well (or at all).
    Should also run the checks to make sure that nothing is None (check_no_attributes_none) and that all keys
    for the command, input, and output dictionaries are all present and matching (check_input_output_command_match)
    """

    @staticmethod
    def validate_job_name(job_name):
        # Since we use job name as container name, need to conform to those standards. Job name must therefore be:
        # 1) Lowercase
        # 2) contain only letters, numbers, and hyphens (and never two hyphens in a row)
        # 3) be between 3 and 63 characters.
        """
        Returns true if job name is good
        :return:
        """
        if job_name.lower() != job_name:
            raise AttributeError('Job name must be entirely lower case.')
        if len(job_name) < 4 or len(job_name) > 63:
            raise AttributeError('Job name must be between 3 and 63 characters. Your job, {name} has {name_length} '
                                 'characters.'.format(name=job_name,
                                                      name_length=len(job_name)))
        if '--' in job_name or job_name.endswith('-'):
            raise AttributeError('Hyphens are allowed in the job name, but you can have no more than one hyphen in '
                                 'a row, and the job name cannot end with a hyphen.')
        if job_name.replace('-', '').isalnum() is False:
            raise AttributeError('Job names must contain only letters, numbers and hyphens. Special characters are '
                                 'not allowed.')
        return True

    @staticmethod
    def create_pool(azurebatch, settings, container_name, vm_size='Standard_D2s_v3', num_nodes=1):
        credentials = ServicePrincipalCredentials(
            client_id=azurebatch.vm_client_id,
            secret=azurebatch.vm_secret,
            tenant=azurebatch.vm_tenant,
            resource='https://batch.core.windows.net/'
        )
        batch_client = batch.BatchServiceClient(credentials, base_url=azurebatch.batch_account_url)
        new_pool = batch.models.PoolAddParameter(
            id=container_name,
            virtual_machine_configuration=batchmodels.VirtualMachineConfiguration(
                image_reference=batchmodels.ImageReference(
                    virtual_machine_image_id=settings.VM_IMAGE,
                ),
                node_agent_sku_id='batch.node.ubuntu 20.04'),
            vm_size=vm_size,
            target_dedicated_nodes=num_nodes,
            target_low_priority_nodes=0
        )
        batch_client.pool.add(new_pool)

    @staticmethod
    def upload_input_to_blob_storage(settings, input_id, input_dict, job_name):
        """
        Uploads input files to blob storage to be used with batch service.
        :return: List of resource files (azure.batch.models.ResourceFile) to be submitted with a task
        """
        # Instantiate our blob service! Maybe better to only do this once?
        resource_files = list()
        blob_service = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                        account_name=settings.AZURE_ACCOUNT_NAME)
        # Create a container for each input request - should be jobname-input, all lower case.
        input_container_name = job_name + '-input' + input_id
        blob_service.create_container(container_name=input_container_name)
        for input_request in input_dict[input_id]:
            # If input request is only one item, just upload that to default dir on cloud
            if len(input_request.split()) == 1:
                if os.path.isdir(input_request):
                    files_to_upload = AzureBatch.recursive_file_list(input_request)
                    last_item_on_path = os.path.split(input_request)[-1]
                    for file_to_upload in files_to_upload:
                        if not input_request.endswith('/'):
                            input_request += '/'
                        upload_to_dir = file_to_upload.split(input_request)[-1]
                        upload_to_dir = os.path.split(upload_to_dir)[0]
                        upload_to_dir = os.path.join(last_item_on_path, upload_to_dir)
                        resource_files.append(AzureBatch.create_resource_file(
                            blob_service=blob_service,
                            file_to_upload=file_to_upload,
                            input_container_name=input_container_name,
                            destination_dir=upload_to_dir))
                else:
                    files_to_upload = glob.glob(input_request)
                    for file_to_upload in files_to_upload:
                        resource_files.append(AzureBatch.create_resource_file(
                            blob_service=blob_service,
                            file_to_upload=file_to_upload,
                            input_container_name=input_container_name))

            # If more than one item, last item is the destination directory on cloud vm that will run analysis.
            if len(input_request.split()) > 1:
                things_to_upload = input_request.split()
                destination_dir = things_to_upload.pop()
                for thing in things_to_upload:
                    if os.path.isdir(thing):
                        files_to_upload = AzureBatch.recursive_file_list(directory=thing)
                        last_item_on_path = os.path.split(thing)[-1]
                        for file_to_upload in files_to_upload:
                            if not thing.endswith('/'):
                                thing += '/'
                            upload_to_dir = file_to_upload.split(thing)[-1]
                            upload_to_dir = os.path.split(upload_to_dir)[0]
                            upload_to_dir = os.path.join(last_item_on_path, upload_to_dir)
                            resource_files.append(AzureBatch.create_resource_file(
                                blob_service=blob_service,
                                file_to_upload=file_to_upload,
                                input_container_name=input_container_name,
                                destination_dir=os.path.join(destination_dir, upload_to_dir)))
                    else:
                        files_to_upload = glob.glob(thing)
                        for file_to_upload in files_to_upload:
                            resource_files.append(AzureBatch.create_resource_file(
                                blob_service=blob_service,
                                file_to_upload=file_to_upload,
                                input_container_name=input_container_name,
                                destination_dir=destination_dir))
        return resource_files

    @staticmethod
    def delete_job(batch_client, job_name):
        """
        Deletes a batch job. Add a check that the job exists before attempting deletion?
        """
        batch_client.job.delete(job_id=job_name)

    @staticmethod
    def delete_pool(batch_client, pool_name):
        """
        Deletes a pool. Raises AttributeError if pool trying to be deleted does not exist.
        """
        if batch_client.pool.exists(pool_id=pool_name):
            batch_client.pool.delete(pool_id=pool_name)
        else:
            raise AttributeError('Pool {} does not exist, and therefore cannot be deleted.'.format(pool_name))

    @staticmethod
    def write_exit_code_file(command, exit_code_file, job_name):
        """
        For various reasons, this script can fail to work. In addition to giving a nice error message to the user,
        we also need to setup the exit code file to say that the task failed in case anything relies on exit codes.
        :param exit_code_file: If None, nothing happens other than the script quitting. Otherwise, should be a string
        that has the path to exit code file. Script will quit at the end of this method.
        """
        if exit_code_file:  # Update exit code file with a non-zero code if upload failed.
            with open(exit_code_file, 'a+') as f:
                for command_id in command:
                    f.write('{},{}\n'.format(job_name + command_id, '1'))
        quit(code=1)

    @staticmethod
    def prepare_cloud_input_resource_files(settings, cloud_input, input_id):
        """
        Creates resource files for input files already in blob storage to be used with batch service.
        :return: List of resource files (azure.batch.models.ResourceFile) to be submitted with a task
        """
        resource_files = list()
        blob_service = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                        account_name=settings.AZURE_ACCOUNT_NAME)
        for input_request in cloud_input[input_id]:
            # Only one thing specified means no destination dir specified
            if len(input_request.split()) == 1:
                container_name = input_request.split('/')[0]
                sas_token = blob_service.generate_container_shared_access_signature(
                    container_name=container_name,
                    permission=BlobPermissions.READ,
                    expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=2))
                pattern = os.path.split(input_request)[1]
                if pattern == '':
                    pattern = '*'
                generator = blob_service.list_blobs(container_name=container_name)
                for blob in generator:
                    if fnmatch.fnmatch(blob.name, pattern):
                        sas_url = blob_service.make_blob_url(container_name=container_name,
                                                             blob_name=blob.name,
                                                             sas_token=sas_token)
                        resource = batchmodels.ResourceFile(file_path=blob.name,
                                                            blob_source=sas_url)
                        resource_files.append(resource)
            else:
                # When more than one input is specified, they all have to be from the same container.
                # This way, we only have to list all blobs once, which makes it actually possible
                # to have many requests coming from containers with lots of blobs
                things_to_upload = input_request.split()
                destination_dir = things_to_upload.pop()
                container_name = things_to_upload[0].split('/')[0]
                generator = blob_service.list_blobs(container_name=container_name)
                blobs = list()
                for item in generator:
                    blobs.append(item)
                for item in things_to_upload:
                    container_name = item.split('/')[0]
                    sas_token = blob_service.generate_container_shared_access_signature(
                        container_name=container_name,
                        permission=BlobPermissions.READ,
                        expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=2))
                    pattern = os.path.split(item)[1]
                    if pattern == '':
                        pattern = '*'
                    for blob in blobs:
                        if fnmatch.fnmatch(blob.name, pattern):
                            sas_url = blob_service.make_blob_url(container_name=container_name,
                                                                 blob_name=blob.name,
                                                                 sas_token=sas_token)
                            resource = batchmodels.ResourceFile(file_path=os.path.join(destination_dir, blob.name),
                                                                blob_source=sas_url)
                            resource_files.append(resource)
        return resource_files

    @staticmethod
    def create_job(batch_client, pool_name, job_name):
        """
        Creates a job. Must have a pool created BEFORE you attempt to run this.
        If a job with the same name already exists, this won't work.
        Returns False is job wasn't able to be created, True if the job create worked.
        """

        if batch_client.pool.exists(pool_id=pool_name):
            job = batch.models.JobAddParameter(id=job_name, pool_info=batch.models.PoolInformation(pool_id=pool_name))
        else:
            raise AttributeError('Job {} was added to a pool that does not exist. Pool must exist before a '
                                 'job can be added to that pool.'.format(job_name))
        try:
            batch_client.job.add(job)
        except batchmodels.batch_error_py3.BatchErrorException:
            return False
        return True

    def create_task(self, batch_client, input_files, command_id, job_name,
                    container_name, task_name):
        blob_service = BlockBlobService(account_key=self.storage_account_key,
                                        account_name=self.storage_account_name)
        # Need an output container created.
        output_container_name = container_name + '-output' + command_id
        blob_service.create_container(container_name=output_container_name)
        sas_token = blob_service.generate_container_shared_access_signature(
            container_name=output_container_name,
            permission=BlobPermissions.WRITE,
            expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=24))
        sas_url = 'https://{}.blob.core.windows.net/{}?{}'.format(self.storage_account_name,
                                                                  output_container_name,
                                                                  sas_token)
        output_files = self.prepare_output_resource_files(sas_url=sas_url,
                                                          output_id=command_id,
                                                          job_name=job_name)
        task = batch.models.TaskAddParameter(
            id=task_name + command_id,
            command_line="/bin/bash -c \"{}\"".format(self.command[command_id]),
            resource_files=input_files,
            output_files=output_files,
            # Add this in so user doesn't have to type the entirety of the path to conda.
            # Completely unclear on why I can't just modify the $PATH in order to make this work.
            environment_settings=[batchmodels.EnvironmentSetting(name='CONDA', value='/usr/bin/miniconda/bin')]
        )
        try:
            batch_client.task.add(job_id=job_name, task=task)
        except batchmodels.batch_error_py3.BatchErrorException:
            return False
        return True

    @staticmethod
    def wait_for_tasks_to_complete(batch_client, job_name):
        # TODO: Add an optional timeout parameter?
        # Check the status of all tasks associated with the job.
        all_tasks_completed = False
        while all_tasks_completed is False:
            tasks = batch_client.task.list(job_name)
            all_tasks_completed = True
            for task in tasks:
                if task.state != batchmodels.TaskState.completed:
                    all_tasks_completed = False
            time.sleep(30)

    @staticmethod
    def check_task_exit_codes(batch_client, job_name):
        exit_codes = dict()
        tasks = batch_client.task.list(job_name)
        for task in tasks:
            exit_codes[task.id] = task.execution_info.exit_code
        return exit_codes

    @staticmethod
    def download_output_files_and_delete_container(settings, output_dir, output_id, job_name):
        output_container = job_name + '-output' + output_id
        blob_service = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                        account_name=settings.AZURE_ACCOUNT_NAME)
        AzureBatch.download_container(blob_service=blob_service,
                                      container_name=output_container,
                                      output_dir=output_dir)
        blob_service.delete_container(container_name=output_container)

    @staticmethod
    def delete_input_container(settings, job_name, input_id):
        input_container = job_name + '-input' + input_id
        blob_service = BlockBlobService(account_key=settings.AZURE_ACCOUNT_KEY,
                                        account_name=settings.AZURE_ACCOUNT_NAME)
        blob_service.delete_container(container_name=input_container)

    def prepare_output_resource_files(self, sas_url, output_id, job_name):
        output_files = list()
        for output_request in self.output[output_id]:
            for output_item in output_request.split():
                # This means we're getting a directory, so need to go recursive
                if output_item.endswith('/'):
                    output_files.append(batchmodels.OutputFile(
                        file_pattern=output_item + '*',
                        destination=batchmodels.OutputFileDestination(
                            container=batchmodels.OutputFileBlobContainerDestination(
                                container_url=sas_url,
                                path=os.path.split(output_item)[0])),
                        upload_options=batchmodels.OutputFileUploadOptions(
                            upload_condition=batchmodels.OutputFileUploadCondition.task_success
                        )))
                    output_files.append(batchmodels.OutputFile(
                        file_pattern=output_item + '/**/*',
                        destination=batchmodels.OutputFileDestination(
                            container=batchmodels.OutputFileBlobContainerDestination(
                                container_url=sas_url,
                                path=os.path.split(output_item)[0].replace('**', ''))),
                        upload_options=batchmodels.OutputFileUploadOptions(
                            upload_condition=batchmodels.OutputFileUploadCondition.task_success
                        )))
                else:
                    output_files.append(batchmodels.OutputFile(
                        file_pattern=output_item,
                        destination=batchmodels.OutputFileDestination(
                            container=batchmodels.OutputFileBlobContainerDestination(
                                container_url=sas_url,
                                path=os.path.split(output_item)[0])),
                        upload_options=batchmodels.OutputFileUploadOptions(
                            upload_condition=batchmodels.OutputFileUploadCondition.task_success
                        )))
        # Also add stdout and stderr.txt log files from the azure container. This is done even if task isn't successful
        output_files.append(batchmodels.OutputFile(
            file_pattern='../stderr.txt',
            destination=batchmodels.OutputFileDestination(
                container=batchmodels.OutputFileBlobContainerDestination(
                    container_url=sas_url,
                    path=job_name + '_' + output_id + '_stderr.txt')),
            upload_options=batchmodels.OutputFileUploadOptions(
                upload_condition=batchmodels.OutputFileUploadCondition.task_completion)
        ))
        output_files.append(batchmodels.OutputFile(
            file_pattern='../stdout.txt',
            destination=batchmodels.OutputFileDestination(
                container=batchmodels.OutputFileBlobContainerDestination(
                    container_url=sas_url,
                    path=job_name + '_' + output_id + '_stdout.txt')),
            upload_options=batchmodels.OutputFileUploadOptions(
                upload_condition=batchmodels.OutputFileUploadCondition.task_completion)
        ))
        return output_files

    def login_to_batch(self):
        """
        Uses credentials stored in object to login to Azure batch.
        :return: an instance of batch_client (azure.batch.batch_service_client)
        """
        credentials = batch_auth.SharedKeyCredentials(self.batch_account_name, self.batch_account_key)
        batch_client = batch.BatchServiceClient(credentials, base_url=self.batch_account_url)
        try:  # Try an operation that will error if the batch client credentials were not correct.
            batch_client.pool.get_all_lifetime_statistics()
        except batchmodels.BatchErrorException:  # This exception occurs if KEY or NAME is incorrect.
            raise AttributeError('Batch client could not be authenticated. Likely cause is your BATCH_ACCOUNT_KEY or'
                                 'BATCH_ACCOUNT_NAME being incorrect.')
        except msrest.exceptions.ClientRequestError:  # This occurs when the provided URL is incorrect.
            raise AttributeError(
                'Batch client could not be authenticated. Check that your BATCH_ACCOUNT_URL is correct.')
        return batch_client

    def __init__(self):
        # Initially, have all these attrs set to None, and set them as needed.
        self.batch_account_name = None
        self.batch_account_key = None
        self.batch_account_url = None
        self.storage_account_name = None
        self.storage_account_key = None
        self.job_name = None
        self.vm_image = None
        self.vm_size = 'Standard_D16s_v3'  # This should be sufficient for essentially anything. User can customize
        # if they really need something bigger (or smaller to save money)
        # Things needed for authentication through active directory, which is apparently necessary.
        self.vm_client_id = None
        self.vm_secret = None
        self.vm_tenant = None
        # Input and output both take the form of dictionaries that contain lists.
        # Keys for the dictionaries are input and output IDs - user can have INPUT_1 and INPUT_2 in the config file,
        # and then we'll get inputs 1 and 2, each having their own files. Same idea for outputs and commands.
        # Any key in input has to be a key in output and command as well, and vice versa (all three ways)
        self.input = dict()
        self.output = dict()
        self.command = dict()
        # Make it so that we can also use things that are already in blob storage.
        # These input file lines should be CLOUDIN:= with _1 or whatever, same is input/output/command.
        # A full proper line would be CLOUDIN:=container_name/ destination_dir
        # This would get everything in container_name and put it into destination_dir, while preserving directory
        # structure if there was any
        self.cloud_input = dict()


class AzureBatch(object):

    @staticmethod
    def main(configuration_file, job_name, settings, output_dir, exit_code_file=None, no_clean=False,
             download_output_files=True, keep_input_container=False, vm_size='Standard_D2s_v3'):
        logging.info('Reading in configuration file {}...'.format(configuration_file))
        azurebatch = AzureBatch.parse_configuration_file(configuration_file)
        AzureBatch.check_no_attributes_none(azurebatch)
        azurebatch.validate_job_name(job_name=job_name)
        AzureBatch.check_input_output_command_match(azurebatch)
        logging.info('Configuration file validated. Creating pool...')
        # Create pool before uploading files - this way, the pool should (hopefully) be created by the time file
        # upload finishes, depending on how large the input files are.
        azurebatch.create_pool(azurebatch=azurebatch,
                               settings=settings,
                               container_name=job_name,
                               vm_size=vm_size,
                               num_nodes=len(azurebatch.command))
        batch_client = azurebatch.login_to_batch()
        logging.info('Uploading files...')
        # TODO: Add progress bar for each file for more user feedback?
        resource_files = dict()
        try:
            for input_id in azurebatch.input:
                resource_files[input_id] = azurebatch.upload_input_to_blob_storage(
                    settings=settings,
                    input_dict=dict(),
                    input_id=input_id,
                    job_name=job_name
                )
        except:  # TODO: Double check the exception that comes up on upload timeout and make more specific.
            logging.error('ERROR: Upload of local files to cloud failed. Please try again.')
            azurebatch.delete_pool(batch_client=batch_client,
                                   pool_name=job_name)
            azurebatch.write_exit_code_file(job_name=job_name,
                                            exit_code_file=exit_code_file,
                                            command=azurebatch.command)

        # Also use cloud files already in blob storage as input, if specified.
        for input_id in azurebatch.cloud_input:
            cloud_resource = azurebatch.prepare_cloud_input_resource_files(
                input_id=input_id,
                cloud_input=azurebatch.cloud_input,
                settings=settings
            )
            for resource in cloud_resource:
                if input_id in resource_files:
                    resource_files[input_id].append(resource)
                else:
                    resource_files[input_id] = [resource]
        logging.info('Running tasks...')
        job_create_successful = azurebatch.create_job(
            batch_client=batch_client,
            pool_name=job_name,
            job_name=job_name)
        if job_create_successful is False:
            logging.error('ERROR: Job was not created successfully. Another job with the same name may already exist.')
            azurebatch.delete_pool(batch_client=batch_client,
                                   pool_name=job_name)
            azurebatch.write_exit_code_file(
                command=azurebatch.command,
                job_name=job_name,
                exit_code_file=exit_code_file)

        for command_id in azurebatch.command:
            azurebatch.create_task(
                batch_client=batch_client,
                input_files=resource_files[command_id],
                command_id=command_id,
                job_name=job_name,
                container_name=job_name,
                task_name=job_name
            )
        # With tasks submitted, wait for all to reach a 'completed' state.
        if no_clean is False:
            azurebatch.wait_for_tasks_to_complete(batch_client=batch_client,
                                                  job_name=job_name)
            task_exit_codes = azurebatch.check_task_exit_codes(batch_client=batch_client,
                                                               job_name=job_name)
            # Clean up resources so that we don't spend exorbitant amounts of money.
            logging.info('Tasks complete! Cleaning up pool...')
            azurebatch.delete_job(batch_client=batch_client,
                                  job_name=job_name)
            azurebatch.delete_pool(batch_client=batch_client,
                                   pool_name=job_name)
            logging.info('Downloading output files...')
            if download_output_files:
                for output_id in azurebatch.output:
                    azurebatch.download_output_files_and_delete_container(settings=settings,
                                                                          job_name=job_name,
                                                                          output_dir=output_dir,
                                                                          output_id=output_id)
            if keep_input_container is False:
                for input_id in azurebatch.input:
                    azurebatch.delete_input_container(settings=settings,
                                                      job_name=job_name,
                                                      input_id=input_id)

            # Check task exit codes. Warn user if the tasks did not have a 0 (success) exit code.
            for task_id in task_exit_codes:
                if exit_code_file:
                    with open(exit_code_file, 'a+') as f:
                        f.write('{},{}\n'.format(task_id, task_exit_codes[task_id]))
                if task_exit_codes[task_id] != 0:
                    logging.error(
                        'ERROR: Task {} did not complete successfully (exit code {}). See the stderr and stdout '
                        'files of that task for more information.'.format(task_id, task_exit_codes[task_id]))
        logging.info('Complete!')

    @staticmethod
    def generate_blob_name(blob_service, file_to_upload, container_name):
        blob_name = os.path.basename(file_to_upload)
        blob_exists = blob_service.exists(container_name=container_name, blob_name=blob_name)
        while blob_exists:
            blob_name = os.path.basename(file_to_upload)
            blob_name += AzureBatch.random_string(string_length=6)
            blob_exists = blob_service.exists(container_name=container_name, blob_name=blob_name)
        return blob_name

    @staticmethod
    def create_resource_file(blob_service, file_to_upload, input_container_name, destination_dir=None):
        """
        Given a file on a local machine, creates a resource file that Azure Batch can work with so that the input
        files will be uploaded to Batch service
        :param blob_service: Instatiated block_blob_service object (azure.storage.blob.BlockBlobService)
        :param file_to_upload: Path to file to upload on local machine.
        :param input_container_name: Name of the container to be used to store the input files. Must already have been
        created.
        :param destination_dir: Destination directory on cloud machine that process will run on. If False,
        will be uploaded to root dir.
        :return: An azure batchmodels resource file (azure.batch.models.ResourceFile)
        """
        # Very occasionally, we might run into problems when recursively uploading a directory that has a file
        # with the same name at different levels in the directory structure. To mitigate that, use the
        # generate_blob_name method, which checks if a blob with the same name exists, and generates a new name
        # for the blob (resource filename will be unaffected) using a random string of 6 characters
        blob_name = AzureBatch.generate_blob_name(blob_service=blob_service,
                                                  file_to_upload=file_to_upload,
                                                  container_name=input_container_name)
        blob_service.create_blob_from_path(container_name=input_container_name,
                                           blob_name=blob_name,
                                           file_path=file_to_upload)
        sas_token = blob_service.generate_container_shared_access_signature(
            container_name=input_container_name,
            permission=BlobPermissions.READ,
            expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=2))
        sas_url = blob_service.make_blob_url(container_name=input_container_name,
                                             blob_name=blob_name,
                                             sas_token=sas_token)
        if destination_dir:
            return batchmodels.ResourceFile(file_path=os.path.join(destination_dir, os.path.split(file_to_upload)[-1]),
                                            blob_source=sas_url)
        else:
            return batchmodels.ResourceFile(file_path=os.path.split(file_to_upload)[-1],
                                            blob_source=sas_url)

    @staticmethod
    def sanitize_id(identifier):
        if identifier.replace('-', '').isalnum() is False:
            raise AttributeError('Input/output/command names must contain only letters, numbers and hyphens. Special '
                                 'characters are not allowed. Your identifier was: {}'.format(identifier))
        return identifier.lower()

    @staticmethod
    def parse_configuration_file(config_file):
        """
        Parse the configuration file a user provides and return an insantiated object that can do all the things - seems
        to most likely be the best way to do things.
        It seems that the best way to do this may, sadly, be to make users write a config file.
        Things we'll need in the config file:
        # Azure-related things - should be able to have these pre-filled for people.
        BATCH_ACCOUNT_NAME=''
        BATCH_ACCOUNT_KEY=''
        BATCH_ACCOUNT_URL=''
        STORAGE_ACCOUNT_NAME = ''
        STORAGE_ACCOUNT_KEY = ''
        # This will be very necessary
        JOB_NAME =
        # Allow multiple input files - each one can be a unix-y mv, with the last arg being a folder to place the files
        in on cloud VM.
        INPUT =
        # Also allow multiple output files - each will get uploaded to blob storage, and optionally download from blob
        storage to user's computer.
        OUTPUT =
        # The command to run on cloud.
        COMMAND =
        # The URL for the VM image user wants to run - will need to have a list somewhere showing what VMs have what
        programs installed
        VM_IMAGE =
        # Have a default VM size that should be sufficient for essentially anything, but allow for custom VMs
        VM_SIZE =
        """
        with open(config_file) as f:
            config_options = f.readlines()

        azurebatch = BatchClass()
        unrecognized_options = list()

        # Go through the input file and parse through all the things.
        # If any options specified are not part of our set of recognized options, boot them out with a message
        for config_option in config_options:
            config_option = config_option.rstrip()
            # Allow comments if line starts with #
            if config_option.startswith('#'):
                continue
            if ':=' not in config_option:  # If line doesn't have our assignment operator in it, issue a warning to user
                # but don't actually crash.
                logging.warning('WARNING: Found a line in configuration file that did not have an option! Line found '
                                'was {}'.format(config_option))
                continue
            x = config_option.split(':=')
            option = x[0]
            parameter = x[1]
            # Unfortunate if structure :(
            if option == 'BATCH_ACCOUNT_NAME':
                azurebatch.batch_account_name = parameter
            elif option == 'BATCH_ACCOUNT_KEY':
                azurebatch.batch_account_key = parameter
            elif option == 'BATCH_ACCOUNT_URL':
                azurebatch.batch_account_url = parameter
            elif option == 'STORAGE_ACCOUNT_NAME':
                azurebatch.storage_account_name = parameter
            elif option == 'STORAGE_ACCOUNT_KEY':
                azurebatch.storage_account_key = parameter
            elif option == 'JOB_NAME':
                azurebatch.job_name = parameter
            elif 'COMMAND' in option:
                if len(option.split('_')) == 2:
                    command_id = AzureBatch.sanitize_id(option.split('_')[1])
                else:
                    command_id = ''
                azurebatch.command[command_id] = parameter
            elif 'INPUT' in option:
                if len(option.split('_')) == 2:
                    input_id = AzureBatch.sanitize_id(option.split('_')[1])
                else:
                    input_id = ''
                if input_id not in azurebatch.input:
                    azurebatch.input[input_id] = [parameter]
                else:
                    azurebatch.input[input_id].append(parameter)
            elif 'CLOUDIN' in option:
                if len(option.split('_')) == 2:
                    input_id = AzureBatch.sanitize_id(option.split('_')[1])
                else:
                    input_id = ''
                if input_id not in azurebatch.cloud_input:
                    azurebatch.cloud_input[input_id] = [parameter]
                else:
                    azurebatch.cloud_input[input_id].append(parameter)
            elif 'OUTPUT' in option:
                if len(option.split('_')) == 2:
                    output_id = AzureBatch.sanitize_id(option.split('_')[1])
                else:
                    output_id = ''
                if output_id not in azurebatch.output:
                    azurebatch.output[output_id] = [parameter]
                else:
                    azurebatch.output[output_id].append(parameter)
            elif option == 'VM_IMAGE':
                azurebatch.vm_image = parameter
            elif option == 'VM_SIZE':
                azurebatch.vm_size = parameter
            elif option == 'VM_CLIENT_ID':
                azurebatch.vm_client_id = parameter
            elif option == 'VM_SECRET':
                azurebatch.vm_secret = parameter
            elif option == 'VM_TENANT':
                azurebatch.vm_tenant = parameter
            else:
                unrecognized_options.append(option)

        # Check that no options were submitted that were not recognized.
        if len(unrecognized_options) > 0:
            raise AttributeError('The following options were specified in configuration file {config_file},'
                                 ' but not recognized: {options}'.format(options=unrecognized_options,
                                                                         config_file=config_file))

        return azurebatch

    @staticmethod
    def recursive_file_list(directory):
        file_list = glob.glob(os.path.join(directory, '**'), recursive=True)
        # Need to delete any directories, because we don't want those.
        items_to_remove = list()
        for item in file_list:
            if os.path.isdir(item):
                items_to_remove.append(item)
        for item in items_to_remove:
            file_list.remove(item)
        return file_list

    @staticmethod
    def check_no_attributes_none(azurebatch_object):
        missing_attributes = list()
        attrs = vars(azurebatch_object)
        for attr in attrs:
            if attrs[attr] is None:
                missing_attributes.append(attr.upper())
            elif type(attrs[attr]) is list:
                if len(attrs[attr]) == 0:
                    missing_attributes.append(attr.upper())
        if len(missing_attributes) > 0:
            raise AttributeError('The following options are required, but were not found in your '
                                 'configuration file: {}'.format(missing_attributes))

    @staticmethod
    def check_input_output_command_match(azurebatch_object):
        # Check input has corresponding outputs and commands.
        for input_id in azurebatch_object.input:
            if input_id not in azurebatch_object.output:
                raise AttributeError('Input ID {} is present, but there is no corresponding output ID.')
            if input_id not in azurebatch_object.command:
                raise AttributeError('Input ID {} is present, but there is no corresponding command ID.')
        # Check output has corresponding inputs and commands.
        for output_id in azurebatch_object.output:
            if output_id not in azurebatch_object.input and output_id not in azurebatch_object.cloud_input:
                raise AttributeError('Output ID {} is present, but there is no corresponding input ID.')
            if output_id not in azurebatch_object.command:
                raise AttributeError('Output ID {} is present, but there is no corresponding command ID.')
        # Check commands have correspoding inputs and outputs
        for command_id in azurebatch_object.command:
            if command_id not in azurebatch_object.input and command_id not in azurebatch_object.cloud_input:
                raise AttributeError('Command ID {} is present, but there is no corresponding input ID.')
            if command_id not in azurebatch_object.output:
                raise AttributeError('Command ID {} is present, but there is no corresponding output ID.')

    @staticmethod
    def download_container(blob_service, container_name, output_dir):
        # https://blogs.msdn.microsoft.com/brijrajsingh/2017/05/27/downloading-a-azure-blob-storage-container-python/
        generator = blob_service.list_blobs(container_name)
        for blob in generator:
            # check if the path contains a folder structure, create the folder structure
            if "/" in blob.name:
                # extract the folder path and check if that folder exists locally, and if not create it
                head, tail = os.path.split(blob.name)
                if os.path.isdir(os.path.join(output_dir, head)):
                    # download the files to this directory
                    blob_service.get_blob_to_path(container_name, blob.name, os.path.join(output_dir, head, tail))
                else:
                    # create the diretcory and download the file to it
                    os.makedirs(os.path.join(output_dir, head))
                    blob_service.get_blob_to_path(container_name, blob.name, os.path.join(output_dir, head, tail))
            else:
                blob_service.get_blob_to_path(container_name, blob.name, os.path.join(output_dir, blob.name))

    @staticmethod
    def random_string(string_length):
        return ''.join(random.choice(string.ascii_letters) for m in range(string_length))
