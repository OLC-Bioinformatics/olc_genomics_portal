from unittest import mock
import os
import tempfile

from django.test import TestCase

from olc_webportalv2.cowbat.methods import AzureBatch, BatchClass


class AzureBatchHelperTests(TestCase):
    def test_sanitize_id_lowercases_valid_identifier(self):
        self.assertEqual(AzureBatch.sanitize_id('Command-1'), 'command-1')

    def test_sanitize_id_rejects_special_characters(self):
        with self.assertRaises(AttributeError):
            AzureBatch.sanitize_id('command_1')

    def test_delete_job_delegates_to_batch_client(self):
        batch_client = mock.Mock()

        BatchClass.delete_job(batch_client, 'job-1')

        batch_client.job.delete.assert_called_once_with(job_id='job-1')

    def test_delete_pool_deletes_existing_pool(self):
        batch_client = mock.Mock()
        batch_client.pool.exists.return_value = True

        BatchClass.delete_pool(batch_client, 'pool-1')

        batch_client.pool.delete.assert_called_once_with(pool_id='pool-1')

    def test_delete_pool_rejects_missing_pool(self):
        batch_client = mock.Mock()
        batch_client.pool.exists.return_value = False

        with self.assertRaises(AttributeError):
            BatchClass.delete_pool(batch_client, 'pool-1')

        self.assertEqual(batch_client.pool.delete.call_count, 0)

    def test_create_job_rejects_missing_pool(self):
        batch_client = mock.Mock()
        batch_client.pool.exists.return_value = False

        with self.assertRaises(AttributeError):
            BatchClass.create_job(batch_client, 'pool-1', 'job-1')

        self.assertEqual(batch_client.job.add.call_count, 0)

    def test_check_task_exit_codes_returns_task_statuses(self):
        batch_client = mock.Mock()
        batch_client.task.list.return_value = [
            mock.Mock(id='task-1', execution_info=mock.Mock(exit_code=0)),
            mock.Mock(id='task-2', execution_info=mock.Mock(exit_code=1)),
        ]

        result = BatchClass.check_task_exit_codes(batch_client, 'job-1')

        self.assertEqual(result, {'task-1': 0, 'task-2': 1})
        batch_client.task.list.assert_called_once_with('job-1')

    def test_prepare_output_resource_files_includes_logs(self):
        batch = BatchClass()
        batch.output = {'1': ['reports/', 'summary.csv']}

        output_files = batch.prepare_output_resource_files(
            sas_url='https://storage.example/output',
            output_id='1',
            job_name='job-1',
        )

        self.assertEqual(len(output_files), 5)
        self.assertEqual(output_files[0].file_pattern, 'reports/*')
        self.assertEqual(output_files[1].file_pattern, 'reports/**/*')
        self.assertEqual(output_files[2].file_pattern, 'summary.csv')
        self.assertEqual(output_files[3].file_pattern, '../stderr.txt')
        self.assertEqual(output_files[3].upload_options.upload_condition,
                         'taskcompletion')

    @mock.patch('olc_webportalv2.cowbat.methods.AzureBatch.random_string', return_value='ABC123')
    def test_generate_blob_name_adds_suffix_when_name_exists(self, random_string):
        blob_service = mock.Mock()
        blob_service.object_exists.side_effect = [True, False]

        result = AzureBatch.generate_blob_name(
            blob_service,
            '/tmp/read.fastq',
            'input-1',
        )

        self.assertEqual(result, 'read.fastqABC123')
        self.assertEqual(blob_service.object_exists.call_count, 2)
        random_string.assert_called_once_with(string_length=6)

    def test_parse_configuration_file_collects_repeated_inputs(self):
        config = '\n'.join([
            '# comment',
            'BATCH_ACCOUNT_NAME:=batch-account',
            'STORAGE_ACCOUNT_NAME:=storage-account',
            'COMMAND_1:=assemble',
            'INPUT_1:=reads/*.fastq',
            'INPUT_1:=reads/*.fq',
            'CLOUDIN_1:=shared/*.csv output',
            'OUTPUT_1:=reports/',
            'VM_SIZE:=Standard_D4s_v3',
            'not an assignment',
        ])
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as config_file:
            config_file.write(config)
            config_path = config_file.name

        try:
            result = AzureBatch.parse_configuration_file(config_path)
        finally:
            os.unlink(config_path)

        self.assertEqual(result.batch_account_name, 'batch-account')
        self.assertEqual(result.storage_account_name, 'storage-account')
        self.assertEqual(result.command, {'1': 'assemble'})
        self.assertEqual(result.input, {'1': ['reads/*.fastq', 'reads/*.fq']})
        self.assertEqual(result.cloud_input, {'1': ['shared/*.csv output']})
        self.assertEqual(result.output, {'1': ['reports/']})
        self.assertEqual(result.vm_size, 'Standard_D4s_v3')

    def test_parse_configuration_file_rejects_unknown_option(self):
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as config_file:
            config_file.write('UNKNOWN_OPTION:=value\n')
            config_path = config_file.name

        try:
            with self.assertRaises(AttributeError):
                AzureBatch.parse_configuration_file(config_path)
        finally:
            os.unlink(config_path)

    def test_check_configuration_requires_values(self):
        batch = BatchClass()

        with self.assertRaises(AttributeError):
            AzureBatch.check_no_attributes_none(batch)

    def test_check_configuration_requires_matching_ids(self):
        batch = BatchClass()
        batch.input = {'1': ['reads.fastq']}
        batch.output = {'1': ['reports/']}
        batch.command = {}

        with self.assertRaises(AttributeError):
            AzureBatch.check_input_output_command_match(batch)

    @mock.patch('olc_webportalv2.common.storage_client.get_storage_client')
    def test_prepare_cloud_input_resource_files_matches_blob_pattern(
        self, blob_service_class
    ):
        blob_service = blob_service_class.return_value
        blob_service.list_objects.return_value = [
            mock.Mock(name='reads/sample_R1.fastq'),
            mock.Mock(name='reads/sample_R2.fastq'),
            mock.Mock(name='notes.txt'),
        ]
        blob_service.list_objects.return_value[0].name = 'reads/sample_R1.fastq'
        blob_service.list_objects.return_value[1].name = 'reads/sample_R2.fastq'
        blob_service.list_objects.return_value[2].name = 'notes.txt'
        blob_service.create_transfer_url.side_effect = (
            lambda container, action, **kwargs: kwargs['name']
        )
        settings = mock.Mock(AZURE_ACCOUNT_KEY='key', AZURE_ACCOUNT_NAME='account')

        result = BatchClass.prepare_cloud_input_resource_files(
            settings=settings,
            cloud_input={'1': ['reads/*_R1.fastq']},
            input_id='1',
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].file_path, 'reads/sample_R1.fastq')
        self.assertEqual(result[0].blob_source, 'reads/sample_R1.fastq')