#!/usr/bin/env python

"""Expose provider-neutral Azure Batch operations through Flask.

The application accepts FoodPort submission requests and exposes operations
for managing Batch jobs, tasks, pools, and files.
"""

# Standard imports
import hashlib
import hmac
import logging
import os
from functools import wraps

from flask import (
    Flask,
    Response,
    jsonify,
    request,
    stream_with_context,
)

# Set up logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
app.config["BATCH_SERVICE_TOKEN"] = os.environ.get("BATCH_SERVICE_TOKEN")


@app.route("/")
def home():
    """Return a greeting message."""
    return "Hello, World!"


@app.route("/submit_batch_request", methods=["POST"])
def submit_batch_request():
    """Accept a FoodPort submission and return its Batch identifiers.

    Tasks run asynchronously; this endpoint does not wait for completion.
    """
    # Get the data from the request
    data = _json_body()
    if data is None:
        return jsonify({"error": "A JSON object is required"}), 400

    # Define the list of required fields
    required_fields = [
        "container",
        "command_file",
        "vm_size",
        "input_file_pattern",
        "download_file_pattern",
        "analysis_type",
    ]

    missing_fields = [field for field in required_fields if field not in data]

    if missing_fields:
        return jsonify(
            {"error": "Missing required fields", "fields": missing_fields}
        ), 400

    try:
        _validate_payload(data)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        result = _provider().submit_batch_request(data)
    except Exception as exc:
        logging.exception("Failed to submit Batch request")
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(
                getattr(exc, "response", None),
                "status_code",
                None,
            )
        if not isinstance(status, int) or not 400 <= status < 500:
            status = 500
        return jsonify(
            {
                "error": str(exc),
                "status": "Failure",
                "pool_id": "None",
                "job_id": "None",
                "tasks": "None",
            }
        ), status
    return jsonify(result)


def _service_token():
    """Return the configured Batch service token or a derived token."""
    token = app.config.get("BATCH_SERVICE_TOKEN") or os.environ.get(
        "BATCH_SERVICE_TOKEN"
    )
    if token:
        return token
    secret_key = app.config.get("SECRET_KEY") or os.environ.get("SECRET_KEY")
    if not secret_key:
        return None
    return hmac.new(
        secret_key.encode("utf-8"),
        b"foodport-batch-service",
        hashlib.sha256,
    ).hexdigest()


@app.before_request
def authenticate_batch_operations():
    """Authenticate Batch API requests while leaving health checks public."""
    if request.path == "/health":
        return None
    expected = _service_token()
    if expected is None:
        return jsonify(
            {"error": "Batch service authentication is not configured"}
        ), 503
    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied, expected)
    ):
        return jsonify({"error": "Authentication required"}), 401
    return None


@app.route("/health", methods=["GET"])
def health():
    """Return the service health status."""
    return jsonify({"status": "ok"})


def _provider():
    """Return the configured provider, initializing it if necessary."""
    injected = app.config.get("BATCH_PROVIDER")
    if injected is not None:
        return injected
    if "batch_provider" not in app.extensions:
        try:
            from .provider import AzureBatchProvider
        except ImportError:
            from provider import AzureBatchProvider

        app.extensions["batch_provider"] = AzureBatchProvider()
    return app.extensions["batch_provider"]


def _json_safe(value):
    """Convert Azure SDK values to JSON-compatible data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    nested_value = getattr(value, "value", value)
    if nested_value is not value:
        return _json_safe(nested_value)
    return str(value)


def _provider_errors(function):
    """Convert provider exceptions into logged JSON error responses."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            error = getattr(exc, "error", None)
            code = getattr(error, "code", None) or getattr(exc, "code", None)
            message = getattr(error, "message", None) or str(exc)
            message = getattr(message, "value", message)
            message = _json_safe(message)
            details = getattr(error, "details", None)
            if details and not isinstance(
                details,
                (dict, list, str, int, float),
            ):
                details = str(details)
            elif isinstance(details, list):
                details = [
                    (
                        {
                            "key": getattr(item, "key", None),
                            "value": getattr(item, "value", None),
                        }
                        if not isinstance(item, dict)
                        else item
                    )
                    for item in details
                ]
            details = _json_safe(details)
            code = _json_safe(code)
            status_code = getattr(exc, "status_code", None)
            if status_code is None:
                status_code = getattr(
                    getattr(exc, "response", None),
                    "status_code",
                    None,
                )
            if status_code == 404 or str(code).lower() in {
                "jobnotfound",
                "poolnotfound",
                "tasknotfound",
                "filenotfound",
                "computenodenotfound",
            }:
                status = 404
            elif status_code and 400 <= status_code < 500:
                status = status_code
            elif status_code:
                status = 502
            elif exc.__class__.__module__.startswith("azure."):
                status = 502
            else:
                status = 500
            if status >= 500:
                logging.exception("Batch provider operation failed")
            else:
                logging.warning(
                    "Batch provider operation returned HTTP %s: %s",
                    status,
                    message,
                )
            payload = {"error": message}
            if code:
                payload["code"] = code
            if details:
                payload["details"] = details
            return jsonify(payload), status

    return wrapped


def _json_body():
    """Return the request JSON object, or ``None`` for invalid input."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def _stream_download(content):
    """Yield byte chunks from supported Azure SDK download responses."""
    if hasattr(content, "content") and not isinstance(
        content,
        (bytes, bytearray, memoryview),
    ):
        candidate = content.content
        if candidate is not None:
            content = candidate
    if hasattr(content, "response") and not hasattr(content, "read"):
        response = content.response
        if hasattr(response, "http_response"):
            content = response.http_response
        elif response is not None:
            content = response
    if isinstance(content, (bytes, bytearray, memoryview)):
        data = bytes(content)
        for offset in range(0, len(data), 64 * 1024):
            yield data[offset : offset + 64 * 1024]
        return
    if hasattr(content, "iter_content"):
        yield from (
            chunk for chunk in content.iter_content(64 * 1024) if chunk
        )
        return
    if hasattr(content, "read"):
        while True:
            chunk = content.read(64 * 1024)
            if not chunk:
                break
            yield chunk
        return
    try:
        iterator = iter(content)
    except TypeError as exc:
        raise TypeError(
            "Batch provider returned an unsupported file response"
        ) from exc
    for chunk in iterator:
        if chunk:
            yield chunk if isinstance(chunk, bytes) else bytes(chunk)


@app.route("/v1/jobs/<job_id>/tasks", methods=["GET"])
@_provider_errors
def list_tasks(job_id):
    """List tasks in a Batch job.

    Args:
        job_id: Azure Batch job ID.

    """
    return jsonify({"items": _provider().list_tasks(job_id)})


@app.route("/v1/jobs/<job_id>/tasks/<task_id>", methods=["GET"])
@_provider_errors
def get_task(job_id, task_id):
    """Return a Batch task.

    Args:
        job_id: Azure Batch job ID.
        task_id: Azure Batch task ID.

    """
    return jsonify(_provider().get_task(job_id, task_id))


@app.route(
    "/v1/jobs/<job_id>/tasks/<task_id>/files",
    methods=["GET"],
)
@_provider_errors
def list_task_files(job_id, task_id):
    """List files produced by a Batch task.

    Args:
        job_id: Azure Batch job ID.
        task_id: Azure Batch task ID.

    """
    recursive_value = request.args.get("recursive", "false").lower()
    if recursive_value not in {"true", "false"}:
        return jsonify({"error": "recursive must be true or false"}), 400
    return jsonify(
        {
            "items": _provider().list_task_files(
                job_id,
                task_id,
                recursive=recursive_value == "true",
            )
        }
    )


@app.route(
    "/v1/jobs/<job_id>/tasks/<task_id>/files/<path:file_path>",
    methods=["GET"],
)
@_provider_errors
def download_task_file(job_id, task_id, file_path):
    """Stream a file produced by a Batch task.

    Args:
        job_id: Azure Batch job ID.
        task_id: Azure Batch task ID.
        file_path: Path of the file within the task directory.

    """
    content = _provider().download_task_file(job_id, task_id, file_path)
    return Response(
        stream_with_context(_stream_download(content)),
        content_type="application/octet-stream",
        direct_passthrough=True,
    )


@app.route("/v1/jobs/<job_id>", methods=["DELETE"])
@_provider_errors
def delete_job(job_id):
    """Delete a Batch job.

    Args:
        job_id: Azure Batch job ID.

    """
    _provider().delete_job(job_id)
    return jsonify({"id": job_id, "status": "deleted"})


@app.route("/v1/jobs/<job_id>/cancel", methods=["POST"])
@_provider_errors
def cancel_job(job_id):
    """Request termination of a Batch job.

    Args:
        job_id: Azure Batch job ID.

    """
    _provider().cancel_job(job_id)
    return jsonify({"id": job_id, "status": "cancellation_requested"}), 202


@app.route("/v1/pools", methods=["GET"])
@_provider_errors
def list_pools():
    """List Batch pools."""
    return jsonify({"items": _provider().list_pools()})


@app.route("/v1/pools/<pool_id>", methods=["GET"])
@_provider_errors
def get_pool(pool_id):
    """Return a Batch pool.

    Args:
        pool_id: Azure Batch pool ID.

    """
    return jsonify(_provider().get_pool(pool_id))


@app.route("/v1/pools/<pool_id>", methods=["DELETE"])
@_provider_errors
def delete_pool(pool_id):
    """Delete a Batch pool.

    Args:
        pool_id: Azure Batch pool ID.

    """
    _provider().delete_pool(pool_id)
    return jsonify({"id": pool_id, "status": "deleted"})


@app.route("/v1/pools/<pool_id>/resize", methods=["POST"])
@_provider_errors
def resize_pool(pool_id):
    """Set a Batch pool's target node counts.

    Args:
        pool_id: Azure Batch pool ID.

    """
    data = _json_body()
    if data is None:
        return jsonify({"error": "A JSON object is required"}), 400
    dedicated = data.get("target_dedicated_nodes")
    low_priority = data.get("target_low_priority_nodes", 0)
    if (
        not isinstance(dedicated, int)
        or isinstance(dedicated, bool)
        or dedicated < 0
    ):
        return jsonify(
            {"error": "target_dedicated_nodes must be a non-negative integer"}
        ), 400
    if (
        not isinstance(low_priority, int)
        or isinstance(low_priority, bool)
        or low_priority < 0
    ):
        return jsonify(
            {
                "error": (
                    "target_low_priority_nodes must be a non-negative integer"
                )
            }
        ), 400
    _provider().resize_pool(pool_id, dedicated, low_priority)
    return jsonify({"id": pool_id, "status": "resizing"}), 202


@app.route("/v1/pools/<pool_id>/nodes", methods=["GET"])
@_provider_errors
def list_nodes(pool_id):
    """List compute nodes in a Batch pool.

    Args:
        pool_id: Azure Batch pool ID.

    """
    return jsonify({"items": _provider().list_nodes(pool_id)})


@app.route(
    "/v1/pools/<pool_id>/nodes/<node_id>/reboot",
    methods=["POST"],
)
@_provider_errors
def reboot_node(pool_id, node_id):
    """Request reboot of a compute node.

    Args:
        pool_id: Azure Batch pool ID.
        node_id: Azure Batch compute node ID.

    """
    data = _json_body()
    if data is None:
        data = {}
    option = data.get("option", "requeue")
    if not isinstance(option, str) or not option.strip():
        return jsonify({"error": "option must be a non-empty string"}), 400
    _provider().reboot_node(pool_id, node_id, option)
    return jsonify({"id": node_id, "status": "rebooting"}), 202


def _validate_payload(data):
    """Validate and normalize the legacy submission pattern fields.

    Args:
        data: Batch submission payload.

    Returns:
        Normalized analysis and file-pattern fields.

    Raises:
        TypeError: If a field has an invalid type or value.

    """
    if not isinstance(data, dict):
        raise TypeError("Batch request payload must be a JSON object")
    normalized = {
        "analysis_type": data.get("analysis_type"),
        "input_patterns": data.get("input_file_pattern"),
        "download_patterns": data.get("download_file_pattern"),
        "output_patterns": data.get("output_file_pattern"),
        "output_container": data.get("output_container"),
        "output_prefix": data.get("output_prefix"),
    }
    for field in ("input_patterns", "download_patterns", "output_patterns"):
        patterns = normalized[field]
        if patterns is not None and not isinstance(patterns, list):
            raise TypeError(f"{field} must be a list or None")
    command = data.get("command_file")
    if not isinstance(command, (str, list)) or (
        isinstance(command, list)
        and any(not isinstance(item, str) for item in command)
    ):
        raise TypeError("command_file must be a string or a list of strings")
    for field in ("container", "vm_size", "analysis_type"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise TypeError(f"{field} must be a non-empty string")
    if "no_tidy" in data and not isinstance(data["no_tidy"], bool):
        raise TypeError("no_tidy must be a boolean")
    environment_variables = data.get("environment_variables")
    if environment_variables is not None and (
        not isinstance(environment_variables, dict)
        or any(
            not isinstance(name, str) or not name or not isinstance(value, str)
            for name, value in environment_variables.items()
        )
    ):
        raise TypeError("environment_variables must map strings to strings")
    return normalized
