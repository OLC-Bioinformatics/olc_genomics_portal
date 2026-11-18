from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from olc_webportalv2.common.batch_client import BatchServiceError
from olc_webportalv2.common.batch_recovery import retry_failed_pool_resize


class BatchPoolRecoveryTests(SimpleTestCase):
    def make_pool(
        self,
        *,
        allocation_state='steady',
        current_dedicated_nodes=0,
        current_low_priority_nodes=0,
        resize_errors=None,
    ):
        return SimpleNamespace(
            allocation_state=SimpleNamespace(value=allocation_state),
            current_dedicated_nodes=current_dedicated_nodes,
            current_low_priority_nodes=current_low_priority_nodes,
            target_dedicated_nodes=0,
            target_low_priority_nodes=0,
            resize_errors=resize_errors or [],
        )

    def make_client(self, pool):
        client = mock.Mock()
        client.pool.get.return_value = pool
        return client

    def retryable_error(self, code='AllocationFailed'):
        return SimpleNamespace(
            code=code,
            message='Resize failed',
            values=[SimpleNamespace(name='reason', value='quota')],
        )

    def test_missing_pool_is_reported_without_retrying(self):
        client = self.make_client(None)
        client.pool.get.side_effect = BatchServiceError(
            404, 'Pool does not exist'
        )

        result = retry_failed_pool_resize(
            batch_client=client,
            pool_id='job-pool',
        )

        self.assertEqual(result['action'], 'pool_missing')
        client.pool.resize.assert_not_called()

    def test_unexpected_inspection_error_is_reported(self):
        client = self.make_client(None)
        client.pool.get.side_effect = BatchServiceError(503, 'service down')

        result = retry_failed_pool_resize(
            batch_client=client,
            pool_id='job-pool',
        )

        self.assertEqual(result['action'], 'inspection_failed')
        self.assertEqual(result['message'], 'service down')

    def test_resize_is_skipped_when_pool_is_not_retryable(self):
        cases = (
            (
                self.make_pool(current_dedicated_nodes=1),
                'nodes_available',
            ),
            (self.make_pool(allocation_state='resizing'), 'resize_in_progress'),
            (self.make_pool(allocation_state='deleting'), 'unsupported_allocation_state'),
            (self.make_pool(), 'no_resize_error'),
        )

        for pool, expected_action in cases:
            with self.subTest(expected_action=expected_action):
                client = self.make_client(pool)
                result = retry_failed_pool_resize(
                    batch_client=client,
                    pool_id='job-pool',
                )
                self.assertEqual(result['action'], expected_action)
                client.pool.resize.assert_not_called()

    def test_non_retryable_resize_error_is_not_retried(self):
        client = self.make_client(
            self.make_pool(resize_errors=[self.retryable_error('OtherError')])
        )

        result = retry_failed_pool_resize(
            batch_client=client,
            pool_id='job-pool',
        )

        self.assertEqual(result['action'], 'non_retryable_resize_error')
        client.pool.resize.assert_not_called()

    @mock.patch('olc_webportalv2.common.batch_recovery.cache.add')
    def test_retry_is_throttled_when_cache_key_already_exists(self, cache_add):
        client = self.make_client(
            self.make_pool(resize_errors=[self.retryable_error()])
        )
        cache_add.return_value = False

        result = retry_failed_pool_resize(
            batch_client=client,
            pool_id='job-pool',
            retry_interval_seconds=60,
        )

        self.assertEqual(result['action'], 'retry_throttled')
        self.assertEqual(result['retryable_codes'], ['AllocationFailed'])
        cache_add.assert_called_once_with(
            'batch-resize-retry:job-pool',
            True,
            timeout=60,
        )
        client.pool.resize.assert_not_called()

    @mock.patch('olc_webportalv2.common.batch_recovery.cache.add')
    def test_retryable_resize_error_retries_with_requested_node_targets(
        self, cache_add
    ):
        client = self.make_client(
            self.make_pool(resize_errors=[self.retryable_error()])
        )
        cache_add.return_value = True

        result = retry_failed_pool_resize(
            batch_client=client,
            pool_id='job-pool',
            target_dedicated_nodes=2,
            target_low_priority_nodes=3,
        )

        self.assertEqual(result['action'], 'resize_retried')
        self.assertEqual(
            result['resize_errors'][0]['details'],
            {'reason': 'quota'},
        )
        client.pool.resize.assert_called_once_with(
            pool_id='job-pool',
            target_dedicated_nodes=2,
            target_low_priority_nodes=3,
        )

    @mock.patch('olc_webportalv2.common.batch_recovery.cache.delete')
    @mock.patch('olc_webportalv2.common.batch_recovery.cache.add')
    def test_failed_resize_request_releases_retry_throttle(
        self, cache_add, cache_delete
    ):
        client = self.make_client(
            self.make_pool(resize_errors=[self.retryable_error()])
        )
        cache_add.return_value = True
        client.pool.resize.side_effect = BatchServiceError(
            503, 'resize unavailable'
        )

        result = retry_failed_pool_resize(
            batch_client=client,
            pool_id='job-pool',
        )

        self.assertEqual(result['action'], 'resize_request_failed')
        self.assertEqual(result['message'], 'resize unavailable')
        cache_delete.assert_called_once_with('batch-resize-retry:job-pool')
