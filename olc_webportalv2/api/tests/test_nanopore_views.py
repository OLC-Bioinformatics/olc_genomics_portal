import re
from unittest.mock import Mock, patch

from django.test import Client, override_settings
from django.contrib.auth.hashers import check_password
from rest_framework.test import APITestCase

from olc_webportalv2.api.models import (
    DesktopAccessToken,
    DesktopPairing,
    EntraIdentity,
)
from olc_webportalv2.cowbat.models import (
    NanoporeFile,
    NanoporeManifest,
    NanoporeProcessingBatch,
    NanoporeRun,
    SequencingRun,
)
from olc_webportalv2.users.models import User


class DesktopPairingTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='pairing-user')

    def test_start_returns_browser_approval_url(self):
        response = self.client.post('/en-ca/api/auth/pairing/start/')

        self.assertEqual(response.status_code, 201)
        self.assertIn('/en-ca/api/auth/pairing/approve/',
                      response.data['approval_url'])
        self.assertEqual(DesktopPairing.objects.count(), 1)
        self.assertEqual(DesktopPairing.objects.get().code_hash, '')

    def test_approval_and_exchange_are_authenticated_and_one_time(self):
        start = self.client.post('/en-ca/api/auth/pairing/start/')
        pairing_id = start.data['pairing_id']
        approval_url = '/en-ca/api/auth/pairing/approve/{0}/'.format(
            pairing_id
        )

        unauthenticated = self.client.get(approval_url)
        self.assertEqual(unauthenticated.status_code, 302)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        csrf_response = csrf_client.post(approval_url)
        self.assertEqual(csrf_response.status_code, 403)

        self.client.force_login(self.user)
        approval = self.client.post(approval_url)
        self.assertEqual(approval.status_code, 200)
        pairing = DesktopPairing.objects.get(pairing_id=pairing_id)
        self.assertTrue(pairing.code_hash)
        self.assertContains(approval, 'one-time code')
        code = re.search(
            r'id="pairing-code">([0-9]{6})<',
            approval.content.decode('utf-8'),
        ).group(1)
        self.assertTrue(check_password(code, pairing.code_hash))

        exchange = self.client.post(
            '/en-ca/api/auth/pairing/exchange/',
            {'pairing_id': pairing_id, 'code': code},
            format='json',
        )
        self.assertEqual(exchange.status_code, 200)
        self.assertEqual(DesktopAccessToken.objects.count(), 1)

        second_exchange = self.client.post(
            '/en-ca/api/auth/pairing/exchange/',
            {'pairing_id': pairing_id, 'code': code},
            format='json',
        )
        self.assertEqual(second_exchange.status_code, 401)


class EntraDesktopExchangeTests(APITestCase):
    @override_settings(
        ENTRA_TENANT_ID='tenant-id',
        ENTRA_API_SCOPE='nanopore.upload',
        ENTRA_DESKTOP_ALLOWED_EMAIL_DOMAINS='inspection.gc.ca',
        ENTRA_DESKTOP_AUTO_CREATE_USERS=True,
        FOODPORT_DESKTOP_TOKEN_TTL_SECONDS=900,
    )
    @patch('olc_webportalv2.api.entra._validate_entra_token')
    def test_exchange_returns_token_that_authenticates_nanopore_api(
        self, validate
    ):
        validate.return_value = {
            'tid': 'tenant-id',
            'oid': 'object-id',
            'preferred_username': 'operator@inspection.gc.ca',
            'name': 'Operator',
        }

        response = self.client.post(
            '/en-ca/api/auth/entra/exchange/',
            {'access_token': 'entra-token'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['expires_in'], 900)
        self.assertEqual(EntraIdentity.objects.count(), 1)
        self.assertEqual(DesktopAccessToken.objects.count(), 1)
        self.assertEqual(response.data['user']['username'],
                         'operator@inspection.gc.ca')

        api_response = self.client.get(
            '/en-ca/api/nanopore/runs/999999/',
            HTTP_AUTHORIZATION='Token {0}'.format(response.data['token']),
        )

        self.assertEqual(api_response.status_code, 404)

    @patch('olc_webportalv2.api.entra._validate_entra_token')
    def test_exchange_returns_generic_unauthorized_error(self, validate):
        validate.side_effect = ValueError('invalid signature')

        response = self.client.post(
            '/en-ca/api/auth/entra/exchange/',
            {'access_token': 'invalid-token'},
            format='json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data, {'detail': 'Entra authentication failed.'}
        )
        self.assertEqual(DesktopAccessToken.objects.count(), 0)


@override_settings(NANOPORE_RESULTS_CONTAINER='nanopore-results')
class NanoporeLatestResultTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='nanopore-api-user')
        self.run = SequencingRun.objects.create(
            user=self.user,
            run_name='260825-nanopore',
        )
        NanoporeRun.objects.create(sequencing_run=self.run)
        self.url = '/en-ca/api/nanopore/runs/{0}/results/latest/'.format(
            self.run.pk
        )

    def test_latest_result_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)

    @patch('olc_webportalv2.api.views.get_storage_client')
    def test_latest_result_returns_not_found_until_published(self, client_class):
        self.client.force_authenticate(user=self.user)
        client_class.return_value.object_exists.return_value = False

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['error'], 'Results are not published yet.')

    @patch('olc_webportalv2.api.views.get_storage_client')
    def test_latest_result_returns_short_lived_read_url(self, client_class):
        self.client.force_authenticate(user=self.user)
        blob_client = Mock()
        blob_client.object_exists.return_value = True
        blob_client.create_transfer_url.return_value = (
            'https://storage.example/results/latest.json?sas'
        )
        client_class.return_value = blob_client

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            'container': 'nanopore-results',
            'blob_name': 'runs/260825-nanopore/manifests/latest.json',
            'url': 'https://storage.example/results/latest.json?sas',
        })
        self.assertEqual(blob_client.create_transfer_url.call_count, 1)
        transfer_args = blob_client.create_transfer_url.call_args
        self.assertEqual(transfer_args.args[:2], ('nanopore-results', 'download'))
        self.assertEqual(
            transfer_args.kwargs['name'],
            'runs/260825-nanopore/manifests/latest.json'
        )

    @patch('olc_webportalv2.api.views.get_storage_client')
    def test_iteration_result_returns_short_lived_read_url(self, client_class):
        manifest = NanoporeManifest.objects.create(
            sequencing_run=self.run,
            files=[],
            sha256='manifest-sha',
            generation=2,
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=self.run,
            manifest=manifest,
            generation=2,
            report_url=(
                'runs/260825-nanopore/iterations/iteration-000002/'
                'manifests/iteration-000002.json'
            ),
        )
        self.client.force_authenticate(user=self.user)
        blob_client = Mock()
        blob_client.object_exists.return_value = True
        blob_client.create_transfer_url.return_value = 'https://storage/iteration'
        client_class.return_value = blob_client

        response = self.client.get(
            '/en-ca/api/nanopore/runs/{0}/results/2/'.format(self.run.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['generation'], 2)
        self.assertEqual(response.data['url'], 'https://storage/iteration')


class NanoporeRunDetailRetryTests(APITestCase):
    def test_detail_exposes_transient_submission_feedback(self):
        user = User.objects.create_user(username='nanopore-retry-user')
        run = SequencingRun.objects.create(
            user=user,
            run_name='260825-retry',
            workflow_state='ready',
            task_retry_count=1,
        )
        NanoporeRun.objects.create(sequencing_run=run)
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[],
            sha256='manifest-sha',
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
            status='submitting',
            error=(
                'Azure GPU capacity is temporarily unavailable. '
                'Retrying submission (attempt 1 of 3).'
            ),
        )
        self.client.force_authenticate(user=user)

        response = self.client.get(
            '/en-ca/api/nanopore/runs/{0}/'.format(run.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['processing']['retryable'], True)
        self.assertEqual(response.data['processing']['retry_count'], 1)
        self.assertEqual(response.data['processing']['retry_limit'], 8)
        self.assertIn('temporarily unavailable', response.data['processing']['message'])


class NanoporeRunCreationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='nanopore-create-user')
        self.client.force_authenticate(user=self.user)
        self.payload = {
            'run_name': '260916-nanopore',
            'barcode_kit': 'SQK-RBK114-24',
            'barcode_values': [1],
            'sample_metadata': {
                'samples': [{
                    'barcode': 1,
                    'seqid': '2026-MIN-0001',
                    'olnid': 'OLN-1',
                }]
            },
        }

    def test_create_returns_run_metadata(self):
        response = self.client.post(
            '/en-ca/api/nanopore/runs/', self.payload, format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['run_name'], '260916-nanopore')
        self.assertEqual(response.data['barcode_values'], [1])
        self.assertEqual(SequencingRun.objects.count(), 1)
        self.assertEqual(NanoporeRun.objects.count(), 1)

    def test_create_rejects_missing_olnid(self):
        payload = dict(self.payload)
        payload['sample_metadata'] = {
            'samples': [{
                'barcode': 1,
                'seqid': '2026-MIN-0001',
            }]
        }

        response = self.client.post(
            '/en-ca/api/nanopore/runs/', payload, format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['sample_metadata'][0],
            'Every sample requires an OLNID.'
        )

    def test_duplicate_name_returns_useful_conflict(self):
        first = self.client.post(
            '/en-ca/api/nanopore/runs/', self.payload, format='json'
        )
        duplicate = self.client.post(
            '/en-ca/api/nanopore/runs/', self.payload, format='json'
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            duplicate.data['run_name'][0],
            'A run with this name already exists.'
        )
        self.assertEqual(SequencingRun.objects.count(), 1)
        self.assertEqual(NanoporeRun.objects.count(), 1)

    @patch('olc_webportalv2.api.views.get_storage_client')
    def test_prepare_uses_run_name_for_blob_prefix(self, blob_service_class):
        run = SequencingRun.objects.create(
            user=self.user,
            run_name='260916-nanopore',
        )
        NanoporeRun.objects.create(sequencing_run=run)
        blob_service = blob_service_class.return_value
        blob_service.create_transfer_url.return_value = 'https://storage/upload'

        response = self.client.post(
            '/en-ca/api/nanopore/runs/{0}/files/prepare/'.format(run.pk),
            {'relative_path': 'pass/sample.pod5', 'size_bytes': 123},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        expected = '260916-nanopore/input/pod5/pass/sample.pod5'
        self.assertEqual(response.data['blob_name'], 'nanopore-runs/' + expected)
        self.assertEqual(
            blob_service.create_transfer_url.call_args.kwargs['name'], expected
        )


class NanoporeFinalizeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='nanopore-finalize-user')
        self.run = SequencingRun.objects.create(
            user=self.user,
            run_name='260825-finalize',
        )
        NanoporeRun.objects.create(sequencing_run=self.run)
        NanoporeFile.objects.create(
            sequencing_run=self.run,
            relative_path='pass/sample.pod5',
            blob_name='nanopore-runs/{0}/input/pod5/pass/sample.pod5'.format(
                self.run.pk
            ),
            size_bytes=123,
            status='uploaded',
        )
        self.url = '/en-ca/api/nanopore/runs/{0}/finalize/'.format(
            self.run.pk
        )

    @patch('olc_webportalv2.api.views.submit_nanopore_batch.apply_async')
    def test_finalize_is_idempotent(self, submit):
        self.client.force_authenticate(user=self.user)

        with patch(
            'olc_webportalv2.api.views.transaction.on_commit',
            side_effect=lambda callback: callback(),
        ):
            first = self.client.post(self.url)
            second = self.client.post(self.url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            first.data['manifest_sha256'], second.data['manifest_sha256']
        )
        self.assertEqual(NanoporeManifest.objects.filter(
            sequencing_run=self.run
        ).count(), 1)
        self.assertEqual(NanoporeProcessingBatch.objects.filter(
            sequencing_run=self.run
        ).count(), 1)
        self.assertEqual(submit.call_count, 1)

    def test_finalize_unknown_run_returns_not_found(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            '/en-ca/api/nanopore/runs/999999/finalize/'
        )

        self.assertEqual(response.status_code, 404)


class NanoporeStreamingTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='nanopore-stream-user')
        self.run = SequencingRun.objects.create(
            user=self.user,
            run_name='260825-streaming',
        )
        NanoporeRun.objects.create(sequencing_run=self.run)
        self.client.force_authenticate(user=self.user)

    @patch('olc_webportalv2.api.views.submit_nanopore_batch.apply_async')
    @patch('olc_webportalv2.api.views.get_storage_client')
    def test_complete_claims_file_and_starts_batch(
        self, blob_service_class, submit
    ):
        nanopore_file = NanoporeFile.objects.create(
            sequencing_run=self.run,
            relative_path='non_target.pod5',
            blob_name='nanopore-runs/{0}/input/pod5/non_target.pod5'.format(
                self.run.pk
            ),
            size_bytes=24120,
        )
        blob_service_class.return_value.get_object.return_value = Mock(size=24120)

        with patch(
            'olc_webportalv2.api.views.transaction.on_commit',
            side_effect=lambda callback: callback(),
        ):
            response = self.client.post(
                '/en-ca/api/nanopore/runs/{0}/files/{1}/complete/'.format(
                    self.run.pk, nanopore_file.pk
                )
            )

        self.assertEqual(response.status_code, 200)
        nanopore_file.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(nanopore_file.status, 'uploaded')
        self.assertEqual(self.run.workflow_state, 'processing')
        self.assertIsNotNone(nanopore_file.processing_batch_id)
        self.assertEqual(NanoporeManifest.objects.count(), 1)
        self.assertEqual(NanoporeProcessingBatch.objects.count(), 1)
        self.assertEqual(submit.call_count, 1)

    def test_finalize_stops_intake_after_streaming_batch_exists(self):
        manifest = NanoporeManifest.objects.create(
            sequencing_run=self.run,
            files=[],
            sha256='manifest-sha',
            generation=1,
            finalized=True,
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=self.run,
            manifest=manifest,
            generation=1,
            status='submitted',
        )
        NanoporeFile.objects.create(
            sequencing_run=self.run,
            relative_path='target.pod5',
            blob_name='nanopore-runs/{0}/input/pod5/target.pod5'.format(
                self.run.pk
            ),
            size_bytes=23904,
            status='uploaded',
        )

        response = self.client.post(
            '/en-ca/api/nanopore/runs/{0}/finalize/'.format(self.run.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.run.refresh_from_db()
        self.run.nanopore_run.refresh_from_db()
        self.assertEqual(self.run.workflow_state, 'stopping')
        self.assertEqual(self.run.nanopore_run.status, 'stopping')
        self.assertIsNotNone(self.run.nanopore_run.termination_requested_at)
        self.assertEqual(NanoporeProcessingBatch.objects.count(), 2)

    @patch('olc_webportalv2.api.views.submit_nanopore_batch.apply_async')
    @patch('olc_webportalv2.api.views.get_storage_client')
    def test_each_completed_file_is_claimed_once_in_a_generation(
        self, blob_service_class, submit
    ):
        blob_service_class.return_value.get_object.return_value = Mock(size=10)
        first = NanoporeFile.objects.create(
            sequencing_run=self.run,
            relative_path='one.pod5',
            blob_name='nanopore-runs/{0}/input/pod5/one.pod5'.format(
                self.run.pk
            ),
            size_bytes=10,
        )
        second = NanoporeFile.objects.create(
            sequencing_run=self.run,
            relative_path='two.pod5',
            blob_name='nanopore-runs/{0}/input/pod5/two.pod5'.format(
                self.run.pk
            ),
            size_bytes=10,
        )
        with patch(
            'olc_webportalv2.api.views.transaction.on_commit',
            side_effect=lambda callback: callback(),
        ):
            self.client.post(
                '/en-ca/api/nanopore/runs/{0}/files/{1}/complete/'.format(
                    self.run.pk, first.pk
                )
            )
            self.client.post(
                '/en-ca/api/nanopore/runs/{0}/files/{1}/complete/'.format(
                    self.run.pk, second.pk
                )
            )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertNotEqual(first.processing_batch_id, second.processing_batch_id)
        self.assertEqual(
            NanoporeProcessingBatch.objects.filter(
                sequencing_run=self.run
            ).count(), 2
        )
        self.assertEqual(submit.call_count, 2)

    @patch('olc_webportalv2.api.views.get_storage_client')
    def test_processing_payload_reports_waiting_message_and_published_reports_only(
        self, blob_service_class
    ):
        blob_service_class.return_value.object_exists.return_value = False
        manifest = NanoporeManifest.objects.create(
            sequencing_run=self.run,
            files=[],
            sha256='manifest-sha',
            generation=1,
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=self.run,
            manifest=manifest,
            generation=1,
            status='pending',
            report_url='runs/260825-streaming/iterations/iteration-000001/',
        )

        response = self.client.get(
            '/en-ca/api/nanopore/runs/{0}/'.format(self.run.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['processing']['message'],
            'Waiting for the processing task to start.',
        )
        self.assertEqual(response.data['processing']['reports'], [])

    @patch('olc_webportalv2.api.views.get_storage_client')
    def test_processing_reports_include_published_manifest_only(
        self, blob_service_class
    ):
        blob_service_class.return_value.object_exists.return_value = True
        manifest = NanoporeManifest.objects.create(
            sequencing_run=self.run,
            files=[],
            sha256='manifest-sha',
            generation=1,
        )
        NanoporeProcessingBatch.objects.create(
            sequencing_run=self.run,
            manifest=manifest,
            generation=1,
            status='complete',
            report_url='runs/260825-streaming/iterations/iteration-000001/'
            'manifests/iteration-000001.json',
        )

        response = self.client.get(
            '/en-ca/api/nanopore/runs/{0}/'.format(self.run.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['processing']['reports']), 1)
        self.assertTrue(
            response.data['processing']['reports'][0]['report']['published']
        )
