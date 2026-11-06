#!/usr/bin/env python

"""Common Azure Batch pool recovery helpers."""

import logging

from django.conf import settings
from django.core.cache import cache

from olc_webportalv2.common.batch_client import BatchServiceError

logger = logging.getLogger(__name__)


DEFAULT_RETRYABLE_RESIZE_CODES = (
    "AccountCoreQuotaReached",
    "AccountLowPriorityCoreQuotaReached",
    "AccountVMSeriesCoreQuotaReached",
    "AllocationFailed",
    "ResizeTimeout",
)


def batch_pool_id_from_job_id(job_id):
    """Return the conventional pool ID for a FoodPort Batch job."""
    if job_id.endswith("-job"):
        return "{0}-pool".format(job_id[:-4])

    return "{0}-pool".format(job_id)


def _batch_resize_error_details(pool):
    """Return normalized resize-error dictionaries for a Batch pool."""
    errors = []

    for error in pool.resize_errors or []:
        details = {}

        for value in error.values or []:
            details[value.name] = value.value

        errors.append(
            {
                "code": error.code or "",
                "message": error.message or "",
                "details": details,
            }
        )

    return errors


def _batch_allocation_state(pool):
    """Return a lowercase Batch allocation-state string."""
    allocation_state = getattr(pool, "allocation_state", None)

    if allocation_state is None:
        return ""

    value = getattr(allocation_state, "value", None)

    if value:
        return str(value).lower()

    state = str(allocation_state).lower()

    if "." in state:
        state = state.rsplit(".", 1)[-1]

    return state


def retry_failed_pool_resize(
    *,
    batch_client,
    pool_id,
    target_dedicated_nodes=1,
    target_low_priority_nodes=0,
    retry_interval_seconds=None,
    retryable_codes=None
):
    """Retry a failed zero-node Azure Batch pool resize.

    This function does not recreate the pool, job, or task. It only submits
    another resize request when the existing pool is steady, has no allocated
    nodes, has a positive node target, and has a recognized resize error.

    Returns:
        dict: A structured result describing the action taken.
    """
    if retry_interval_seconds is None:
        retry_interval_seconds = getattr(
            settings,
            "BATCH_RESIZE_RETRY_INTERVAL_SECONDS",
            1800,
        )

    if retryable_codes is None:
        retryable_codes = getattr(
            settings,
            "BATCH_RETRYABLE_RESIZE_ERROR_CODES",
            DEFAULT_RETRYABLE_RESIZE_CODES,
        )

    retryable_codes = set(retryable_codes)

    try:
        pool = batch_client.pool.get(pool_id=pool_id)
    except BatchServiceError as exc:
        message = str(exc).lower()

        if "does not exist" in message or "not found" in message:
            return {
                "action": "pool_missing",
                "pool_id": pool_id,
                "message": str(exc),
            }

        logger.exception(
            "Unable to inspect Azure Batch pool %s",
            pool_id,
        )

        return {
            "action": "inspection_failed",
            "pool_id": pool_id,
            "message": str(exc),
        }

    allocation_state = _batch_allocation_state(pool)
    current_dedicated = pool.current_dedicated_nodes or 0
    current_low_priority = pool.current_low_priority_nodes or 0
    target_dedicated = pool.target_dedicated_nodes or 0
    target_low_priority = pool.target_low_priority_nodes or 0
    errors = _batch_resize_error_details(pool)

    result = {
        "action": "none",
        "pool_id": pool_id,
        "allocation_state": allocation_state,
        "current_dedicated_nodes": current_dedicated,
        "target_dedicated_nodes": target_dedicated,
        "current_low_priority_nodes": current_low_priority,
        "target_low_priority_nodes": target_low_priority,
        "resize_errors": errors,
    }

    if current_dedicated > 0 or current_low_priority > 0:
        result["action"] = "nodes_available"
        return result

    if allocation_state in ("resizing", "stopping"):
        result["action"] = "resize_in_progress"
        return result

    if allocation_state != "steady":
        result["action"] = "unsupported_allocation_state"
        return result

    if not errors:
        result["action"] = "no_resize_error"
        return result

    error_codes = set(error["code"] for error in errors if error.get("code"))

    matched_codes = sorted(error_codes.intersection(retryable_codes))

    if not matched_codes:
        result["action"] = "non_retryable_resize_error"

        logger.error(
            "Batch pool %s has non-retryable resize errors: %s",
            pool_id,
            errors,
        )

        return result

    cache_key = "batch-resize-retry:{0}".format(pool_id)

    retry_permitted = cache.add(
        cache_key,
        True,
        timeout=retry_interval_seconds,
    )

    if not retry_permitted:
        result["action"] = "retry_throttled"
        result["retryable_codes"] = matched_codes
        return result

    try:
        batch_client.pool.resize(
            pool_id=pool_id,
            target_dedicated_nodes=target_dedicated_nodes,
            target_low_priority_nodes=target_low_priority_nodes,
        )
    except BatchServiceError as exc:
        cache.delete(cache_key)

        logger.exception(
            "Unable to retry Azure Batch pool resize for %s",
            pool_id,
        )

        result["action"] = "resize_request_failed"
        result["message"] = str(exc)

        return result

    result["action"] = "resize_retried"
    result["retryable_codes"] = matched_codes

    logger.warning(
        "Retried Azure Batch pool resize: "
        "pool=%s errors=%s target_dedicated=%s "
        "target_low_priority=%s",
        pool_id,
        matched_codes,
        target_dedicated_nodes,
        target_low_priority_nodes,
    )

    return result


def _batch_task_exit_codes(*, batch_client, batch_job_name):
    """Return Batch task IDs and exit codes."""
    results = []

    for cloud_task in batch_client.task.list(batch_job_name):
        execution_info = cloud_task.execution_info

        results.append(
            {
                "task_id": cloud_task.id,
                "exit_code": (
                    execution_info.exit_code if execution_info is not None else None
                ),
            }
        )

    return results