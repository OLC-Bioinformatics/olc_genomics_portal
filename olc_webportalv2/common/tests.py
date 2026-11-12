import json
import logging
from unittest import mock

from django.test import SimpleTestCase

from olc_webportalv2.common.methods import (
    _redact_sas_urls,
    generic_api_submit,
)
from olc_webportalv2.common.benchmarks import (
    EXCLUSIVITY_BENCHMARK_CHOICES,
    INCLUSIVITY_BENCHMARK_CHOICES,
    benchmark_archive_info,
    select_benchmark_blob,
    validate_benchmark_panel,
)


class BenchmarkArchiveTests(SimpleTestCase):
    def test_bds_listeria_is_registered(self):
        self.assertIn(
            ('bds-listeria', 'BDS-Listeria'),
            INCLUSIVITY_BENCHMARK_CHOICES,
        )

    def test_bds_stec_is_available_for_both_panels(self):
        choice = ('bds-stec', 'BDS-STEC')
        self.assertIn(choice, INCLUSIVITY_BENCHMARK_CHOICES)
        self.assertIn(choice, EXCLUSIVITY_BENCHMARK_CHOICES)
        self.assertEqual(
            validate_benchmark_panel(
                benchmark_name='bds-stec',
                panel='inclusivity',
            ),
            'bds-stec',
        )
        self.assertEqual(
            validate_benchmark_panel(
                benchmark_name='bds-stec',
                panel='exclusivity',
            ),
            'bds-stec',
        )

    def test_shigella_is_exclusivity_only(self):
        self.assertIn(
            ('shigella-exclusivity', 'Shigella-Exclusivity'),
            EXCLUSIVITY_BENCHMARK_CHOICES,
        )
        self.assertNotIn(
            ('shigella-exclusivity', 'Shigella-Exclusivity'),
            INCLUSIVITY_BENCHMARK_CHOICES,
        )
        self.assertEqual(
            validate_benchmark_panel(
                benchmark_name='shigella-exclusivity',
                panel='exclusivity',
            ),
            'shigella-exclusivity',
        )
        with self.assertRaises(ValueError):
            validate_benchmark_panel(
                benchmark_name='shigella-exclusivity',
                panel='inclusivity',
            )

    def test_shigella_archive_selection_uses_highest_version(self):
        selected = select_benchmark_blob(
            blob_names=[
                'shigella-exclusivity-1.0.0.zip',
                'shigella-exclusivity-1.1.0.zip',
                'shigella-exclusivity.zip',
            ],
            benchmark_name='shigella-exclusivity',
        )
        self.assertEqual(
            selected['blob_name'],
            'shigella-exclusivity-1.1.0.zip',
        )

    def test_archive_info_accepts_short_and_date_versions(self):
        for blob_name, version in (
            ('listeria-1.2.zip', '1.2'),
            ('stx-20260813.zip', '20260813'),
            ('ncbi-salmonella-1.2.0.zip', '1.2.0'),
        ):
            info = benchmark_archive_info(blob_name=blob_name)
            self.assertEqual(info['version'], version)
            self.assertEqual(info['blob_name'], blob_name)

    def test_selector_uses_highest_supported_version(self):
        selected = select_benchmark_blob(
            blob_names=[
                'ncbi-salmonella-1.2.zip',
                'ncbi-salmonella-1.10.zip',
                'ncbi-salmonella.zip',
            ],
            benchmark_name='ncbi-salmonella',
        )
        self.assertEqual(selected['blob_name'], 'ncbi-salmonella-1.10.zip')


class CommonMethodTests(SimpleTestCase):
    def test_redact_sas_urls_preserves_nested_structure(self):
        value = {
            'url': 'https://storage.example/container/blob?sig=secret',
            'items': [
                'https://storage.example/a?token=hidden',
                ('unchanged', 'https://storage.example/b?sig=another'),
            ],
        }

        redacted = _redact_sas_urls(value=value)

        self.assertEqual(
            redacted['url'],
            'https://storage.example/container/blob?[REDACTED]',
        )
        self.assertEqual(
            redacted['items'][0],
            'https://storage.example/a?[REDACTED]',
        )
        self.assertIsInstance(redacted['items'][1], tuple)
        self.assertEqual(redacted['items'][1][0], 'unchanged')
        self.assertEqual(
            redacted['items'][1][1],
            'https://storage.example/b?[REDACTED]',
        )

    @mock.patch('olc_webportalv2.common.methods.requests.post')
    def test_generic_api_submit_builds_payload_and_returns_json(self, post):
        response = mock.Mock(
            status_code=200,
            content=b'{"job_id": "job-1"}',
            text='{"job_id": "job-1"}',
            headers={'content-type': 'application/json; charset=utf-8'},
            url='https://batch.example/jobs',
            elapsed=0,
            reason='OK',
        )
        response.json.return_value = {'job_id': 'job-1'}
        post.return_value = response

        result = generic_api_submit.run(
            command='echo run',
            container_name='job-1',
            vm_size='Standard_D4s_v3',
            analysis_type='GENESEEKR',
            input_file_pattern=[],
            unique_id='FoodPort',
            download_file_pattern=['reports/*'],
            log_prefix='logs',
        )

        self.assertEqual(result, {'job_id': 'job-1'})
        self.assertEqual(post.call_count, 1)
        request = post.call_args[1]
        self.assertEqual(request['timeout'], 600)
        payload = json.loads(request['data'])
        self.assertEqual(payload['container'], 'job-1')
        self.assertEqual(payload['command_file'], 'echo run')
        self.assertEqual(payload['vm_size'], 'Standard_D4s_v3')
        self.assertIsNone(payload['input_file_pattern'])
        self.assertEqual(payload['download_file_pattern'], ['reports/*'])
        self.assertEqual(payload['analysis_type'], 'GENESEEKR')
        self.assertEqual(payload['unique_id'], 'FoodPort')
        self.assertEqual(payload['log_prefix'], 'logs')
        self.assertEqual(response.raise_for_status.call_count, 1)

    @mock.patch('olc_webportalv2.common.methods.requests.post')
    def test_generic_api_submit_rejects_non_json_success_response(self, post):
        response = mock.Mock(
            status_code=200,
            content=b'accepted',
            text='accepted',
            headers={'content-type': 'text/plain'},
            url='https://batch.example/jobs',
            elapsed=0,
            reason='OK',
        )
        post.return_value = response

        with self.assertRaises(RuntimeError):
            generic_api_submit.run(
                command='echo run',
                container_name='job-1',
                vm_size='Standard_D4s_v3',
            )

        self.assertEqual(response.raise_for_status.call_count, 1)

    @mock.patch('olc_webportalv2.common.methods.requests.post')
    def test_generic_api_submit_propagates_http_errors(self, post):
        response = mock.Mock(
            status_code=502,
            content=b'gateway failure',
            text='gateway failure',
            headers={'content-type': 'text/plain'},
            url='https://batch.example/jobs',
            elapsed=0,
            reason='Bad Gateway',
        )
        response.raise_for_status.side_effect = RuntimeError('bad gateway')
        post.return_value = response

        with self.assertRaises(RuntimeError):
            generic_api_submit.run(
                command='echo run',
                container_name='job-1',
                vm_size='Standard_D4s_v3',
            )

        self.assertEqual(response.raise_for_status.call_count, 1)

    @mock.patch('olc_webportalv2.common.methods.requests.post')
    def test_generic_api_submit_redacts_sas_urls_from_logs(self, post):
        response = mock.Mock(
            status_code=200,
            content=b'{"job_id": "job-1"}',
            text='{"job_id": "job-1"}',
            headers={'content-type': 'application/json'},
            url='https://batch.example/jobs',
            elapsed=0,
            reason='OK',
        )
        response.json.return_value = {'job_id': 'job-1'}
        post.return_value = response

        with self.assertLogs(level=logging.INFO) as logs:
            generic_api_submit.run(
                command='curl https://storage.example/blob?sig=secret-value',
                container_name='job-1',
                vm_size='Standard_D4s_v3',
            )

        output = '\n'.join(logs.output)
        self.assertNotIn('secret-value', output)
        self.assertIn('https://storage.example/blob?[REDACTED]', output)
