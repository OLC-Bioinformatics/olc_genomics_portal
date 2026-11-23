"""Azure Batch adapter used only by the internal Batch service."""

import fnmatch
import hashlib
import hmac
import logging
import os
import posixpath
import uuid
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


class SubmissionError(ValueError):
    """Raised for invalid or unsupported submission requests."""

    status_code = 400


class StorageServiceClient:
    """Small Batch-side client for the provider-neutral Storage API."""

    def __init__(self, base_url=None, token=None, session=None, timeout=None):
        """Create a Storage API client.

        Args:
            base_url: Storage service URL; defaults to the environment setting.
            token: Bearer token; defaults to configured service credentials.
            session: Requests-compatible session for HTTP calls.
            timeout: Request timeout in seconds.

        """
        self.base_url = (
            base_url
            or os.environ.get("STORAGE_SERVICE_URL")
            or "http://storage:5001"
        ).rstrip("/")
        self.token = token or os.environ.get("STORAGE_SERVICE_TOKEN")
        if not self.token:
            secret_key = os.environ.get("SECRET_KEY")
            if secret_key:
                self.token = hmac.new(
                    secret_key.encode("utf-8"),
                    b"foodport-storage-service",
                    hashlib.sha256,
                ).hexdigest()
        if not self.token:
            raise RuntimeError(
                "Storage service authentication is not configured."
            )
        self.session = session or requests.Session()
        self.timeout = timeout or float(
            os.environ.get("STORAGE_REQUEST_TIMEOUT", "600")
        )

    def _request(self, method, path, **kwargs):
        """Send an authenticated request to the Storage API.

        Args:
            method: HTTP method.
            path: API endpoint path.
            **kwargs: Additional arguments passed to the session request.

        Returns:
            The successful HTTP response.

        """
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = "Bearer " + self.token
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Storage service request failed: {exc}"
            ) from exc
        if not response.ok:
            try:
                message = response.json().get("error", response.text)
            except ValueError:
                message = response.text
            error = RuntimeError(
                f"Storage service returned {response.status_code}: {message}"
            )
            error.status_code = response.status_code
            raise error
        return response

    def ensure_container(self, container):
        """Ensure that a storage container exists.

        Args:
            container: Container name.

        """
        self._request(
            "PUT",
            f"/v1/containers/{quote(container, safe='')}",
        )

    def list_objects(self, container, prefix=None):
        """List objects in a container, optionally filtered by prefix.

        Args:
            container: Container name.
            prefix: Optional object-name prefix.

        Returns:
            The Storage API's object list.

        """
        response = self._request(
            "GET",
            f"/v1/containers/{quote(container, safe='')}/objects",
            params={"prefix": prefix} if prefix else None,
        )
        return response.json()["items"]

    def copy_object(
        self,
        source_container,
        source_name,
        destination_container,
        destination_name,
    ):
        """Copy an object between containers.

        Args:
            source_container: Name of the source container.
            source_name: Name of the source object.
            destination_container: Name of the destination container.
            destination_name: Name to assign to the copied object.

        """
        self._request(
            "POST",
            "/v1/objects/copy",
            json={
                "source_container": source_container,
                "source_name": source_name,
                "destination_container": destination_container,
                "destination_name": destination_name,
            },
        )

    def create_transfer_url(self, container, action, expires_in_seconds):
        """Create a temporary URL for an operation on a container.

        Args:
            container: Container name.
            action: Permitted operation, such as ``read`` or ``write``.
            expires_in_seconds: URL lifetime in seconds.

        Returns:
            The generated transfer URL.

        """
        response = self._request(
            "POST",
            "/v1/transfer-urls",
            json={
                "container": container,
                "action": action,
                "expires_in_seconds": expires_in_seconds,
            },
        )
        return response.json()["url"]


def _value(value):
    """Unwrap Azure SDK values while preserving ordinary values."""
    if value is None:
        return None
    return getattr(value, "value", value)


def _get(item, key, default=None):
    """Read a dictionary key or object attribute with a default."""
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _isoformat(value):
    """Convert date-like values to ISO 8601 strings."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _resize_error(error):
    """Serialize an Azure Batch resize error into response data."""
    details = _get(error, "details", {}) or {}
    if not isinstance(details, dict):
        details = {
            _get(detail, "name", _get(detail, "key", "")): _get(
                detail, "value"
            )
            for detail in details
        }
    return {
        "code": _value(_get(error, "code")) or "",
        "message": _get(error, "message") or "",
        "details": details,
    }


def _boolean_setting(name):
    """Read an optional boolean environment setting."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean value")


def _batch_request_id(error):
    """Return the Azure request ID when the SDK exposes response headers."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    return (
        headers.get("request-id")
        or headers.get("x-ms-request-id")
        or headers.get("Request-Id")
    )


def _log_batch_failure(operation, error, **context):
    """Log Azure Batch failure context without swallowing the exception."""
    azure_error = getattr(error, "error", None)
    error_code = (
        _value(_get(azure_error, "code"))
        or _value(_get(error, "code"))
        or ""
    )
    logger.exception(
        "Azure Batch operation failed operation=%s request_id=%s "
        "error_code=%s context=%s",
        operation,
        _batch_request_id(error),
        error_code,
        context,
    )


class AzureBatchProvider:
    """Translate Azure Batch SDK operations to provider-neutral data."""

    def __init__(self, client=None, storage_client=None):
        """Initialize the provider with optional service clients.

        Args:
            client: Azure Batch client; created from managed identity if
                absent.
            storage_client: Storage API client; created on first use if absent.

        """
        self.client = client or self._create_client()
        self.storage_client = storage_client

    @staticmethod
    def _create_client():
        """Create an Azure Batch client using managed identity."""
        from azure.batch import BatchServiceClient
        from msrestazure.azure_active_directory import MSIAuthentication

        endpoint = (
            os.environ.get("BATCH_ACCOUNT_URL")
            or os.environ.get("AZURE_BATCH_ACCOUNT_URL")
            or os.environ.get("AZURE_BATCH_URL")
        )
        if not endpoint:
            raise RuntimeError("Azure Batch service URL is not configured")
        credentials = MSIAuthentication(
            resource="https://batch.core.windows.net/"
        )
        return BatchServiceClient(credentials, endpoint)

    def _storage(self):
        """Return the configured Storage API client."""
        if self.storage_client is None:
            self.storage_client = StorageServiceClient()
        return self.storage_client

    @staticmethod
    def _submission_settings(analysis_type):
        """Validate and return the environment settings for an analysis.

        Args:
            analysis_type: FoodPort analysis name.

        Returns:
            The VM image, node agent SKU, subnet, storage account, and key.

        Raises:
            SubmissionError: If the analysis type is unsupported.
            RuntimeError: If required environment settings are missing.

        """
        image_settings = {
            "COWBAT": ("VM_IMAGE", "COWBAT_NODE_AGENT_SKU"),
            "AmpliSeq": ("AMPLISEQ_IMAGE", "AMPLISEQ_NODE_AGENT_SKU"),
            "COWSNPhR": ("COWSNPHR_IMAGE", "COWSNPHR_NODE_AGENT_SKU"),
            "Nanopore": ("NANOPORE_IMAGE", "NANOPORE_NODE_AGENT_SKU"),
        }
        setting_names = image_settings.get(analysis_type)
        if setting_names is None:
            raise SubmissionError(
                f"Unsupported analysis_type: {analysis_type}"
            )
        image = os.environ.get(setting_names[0])
        node_agent = os.environ.get(setting_names[1])
        subnet = os.environ.get("BATCH_ACCOUNT_SUBNET")
        storage_account = os.environ.get(
            "AZURE_ACCOUNT_NAME"
        ) or os.environ.get("STORAGE_ACCOUNT_NAME")
        storage_account_key = os.environ.get("AZURE_ACCOUNT_KEY")
        missing = [
            name
            for name, value in (
                (setting_names[0], image),
                (setting_names[1], node_agent),
                ("BATCH_ACCOUNT_SUBNET", subnet),
                ("AZURE_ACCOUNT_NAME", storage_account),
                ("AZURE_ACCOUNT_KEY", storage_account_key),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Batch submission configuration is missing: "
                + ", ".join(missing)
            )
        return image, node_agent, subnet, storage_account, storage_account_key

    @staticmethod
    def _pattern_tokens(value):
        """Parse an input pattern and its optional destination directory.

        Args:
            value: String pattern or a one/two-item list of strings.

        Returns:
            The source pattern and destination directory.

        Raises:
            SubmissionError: If the pattern has an invalid shape.

        """
        if isinstance(value, str):
            tokens = value.split()
        elif isinstance(value, list) and all(
            isinstance(token, str) for token in value
        ):
            tokens = value
        else:
            raise SubmissionError(
                "Each input_file_pattern item must be a string "
                "or list of strings"
            )
        if not 1 <= len(tokens) <= 2 or not tokens[0]:
            raise SubmissionError(
                "Each input_file_pattern item must contain a source pattern "
                "and an optional destination directory"
            )
        return tokens[0], tokens[1] if len(tokens) == 2 else ""

    @staticmethod
    def _pattern_prefix(pattern):
        """Return the non-wildcard directory prefix of an object pattern."""
        wildcard = min(
            (
                pattern.find(character)
                for character in ("*", "?", "[")
                if character in pattern
            ),
            default=len(pattern),
        )
        prefix = pattern[:wildcard]
        if wildcard < len(pattern):
            prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        return prefix

    @staticmethod
    def _matches_object_pattern(name, pattern):
        """Check whether an object name matches a path-aware pattern."""
        pattern_directory, filename_pattern = posixpath.split(
            pattern.rstrip("/")
        )
        object_directory, filename = posixpath.split(name)
        if pattern_directory:
            relative_directory = posixpath.relpath(
                object_directory,
                pattern_directory,
            )
            if relative_directory == ".." or relative_directory.startswith(
                "../"
            ):
                return False
        elif object_directory:
            return False
        return fnmatch.fnmatchcase(filename, filename_pattern)

    def _stage_inputs(self, container, patterns):
        """Copy matching input objects into the submission container.

        Args:
            container: Destination container for the submission.
            patterns: Input patterns and optional destination directories.

        Raises:
            SubmissionError: If a pattern is invalid or matches no objects.

        """
        storage = self._storage()
        storage.ensure_container(container)
        for item in patterns or []:
            source_pattern, destination = self._pattern_tokens(item)
            source_container, separator, object_pattern = (
                source_pattern.partition("/")
            )
            if not separator or not object_pattern:
                raise SubmissionError(
                    "Input patterns must use container/object-path format"
                )
            if object_pattern.endswith("/"):
                object_pattern += "*"
            objects = storage.list_objects(
                source_container,
                self._pattern_prefix(object_pattern),
            )
            matches = [
                blob["name"]
                for blob in objects
                if self._matches_object_pattern(blob["name"], object_pattern)
            ]
            if not matches:
                raise SubmissionError(
                    f"No input objects matched {source_pattern}"
                )
            if source_container == container:
                continue
            for source_name in matches:
                destination_name = posixpath.join(
                    destination.rstrip("/"),
                    posixpath.basename(source_name),
                )
                storage.copy_object(
                    source_container,
                    source_name,
                    container,
                    destination_name,
                )

    def submit_batch_request(self, data):
        """Create an Azure pool, job, and tasks for a FoodPort request.

        Args:
            data: Validated submission parameters.

        Returns:
            Pool ID, job ID, task IDs, and submission status.

        Raises:
            SubmissionError: If the request is unsupported or invalid.
            RuntimeError: If required Batch or Storage settings are missing.

        """
        from azure.batch import models

        if data.get("no_tidy"):
            raise SubmissionError(
                "no_tidy is not supported for asynchronous "
                "FoodPort submissions"
            )
        command_value = data["command_file"]
        commands = (
            command_value.splitlines()
            if isinstance(command_value, str)
            else command_value
        )
        commands = [command for command in commands if command.strip()]
        if not commands:
            raise SubmissionError(
                "command_file must contain at least one command"
            )

        container = data["container"]
        vm_size = data["vm_size"]
        analysis_type = data["analysis_type"]
        (
            image,
            node_agent,
            subnet,
            storage_account,
            storage_account_key,
        ) = self._submission_settings(analysis_type)
        security_type = (os.environ.get("BATCH_SECURITY_TYPE") or "").strip()
        if not security_type:
            security_type = "trustedLaunch"
        elif security_type.lower() == "none":
            security_type = ""
        secure_boot_enabled = _boolean_setting("BATCH_SECURE_BOOT_ENABLED")
        v_tpm_enabled = _boolean_setting("BATCH_VTPM_ENABLED")
        self._stage_inputs(container, data.get("input_file_pattern"))

        unique_id = data.get("unique_id") or uuid.uuid4().hex[:8]
        if unique_id == "FoodPort":
            pool_id = job_id = task_id = container
        else:
            pool_id = f"{container}-{unique_id}-pool"
            job_id = f"{container}-{unique_id}-job"
            task_id = f"{container}-{unique_id}-task"

        uefi_settings = None
        if secure_boot_enabled is not None or v_tpm_enabled is not None:
            uefi_settings = models.UefiSettings(
                secure_boot_enabled=secure_boot_enabled,
                v_tpm_enabled=v_tpm_enabled,
            )
        security_profile = (
            models.SecurityProfile(
                security_type=security_type,
                uefi_settings=uefi_settings,
            )
            if security_type
            else None
        )
        vm_configuration = {
            "image_reference": models.ImageReference(
                virtual_machine_image_id=image,
            ),
            "node_agent_sku_id": node_agent,
            "security_profile": security_profile,
        }
        pool = models.PoolAddParameter(
            id=pool_id,
            virtual_machine_configuration=models.VirtualMachineConfiguration(
                **vm_configuration,
            ),
            vm_size=vm_size,
            network_configuration=models.NetworkConfiguration(
                subnet_id=subnet,
                public_ip_address_configuration=(
                    models.PublicIPAddressConfiguration(
                        provision="noPublicIPAddresses",
                    )
                ),
            ),
            target_dedicated_nodes=1,
            mount_configuration=[
                models.MountConfiguration(
                    azure_blob_file_system_configuration=(
                        models.AzureBlobFileSystemConfiguration(
                            account_name=storage_account,
                            account_key=storage_account_key,
                            container_name=container,
                            relative_mount_path=container,
                            blobfuse_options=(
                                "--file-cache-timeout-in-seconds=240 "
                                "--attr-cache-timeout=240 "
                                "--entry-timeout=240 "
                                "--negative-timeout=120 "
                                "--tmp-path=/mnt/resource/blobfuse2_cache "
                                "--cache-size-mb=2048 "
                                "--log-level=LOG_WARNING "
                                "--use-attr-cache=true "
                                "--max-concurrency=64 "
                            ),
                        )
                    )
                )
            ],
        )
        pool_context = {
            "pool_id": pool_id,
            "vm_size": vm_size,
            "image": image,
            "node_agent_sku_id": node_agent,
            "subnet": subnet,
            "mount_auth": "account_key",
            "security_type": security_type or None,
            "secure_boot_enabled": secure_boot_enabled,
            "v_tpm_enabled": v_tpm_enabled,
        }
        logger.info("Creating Azure Batch pool context=%s", pool_context)
        try:
            self.client.pool.add(pool)
        except Exception as exc:
            _log_batch_failure("pool.add", exc, **pool_context)
            raise
        logger.info(
            "Azure Batch pool creation accepted pool_id=%s",
            pool_id,
        )
        try:
            self.client.job.add(
                models.JobAddParameter(
                    id=job_id,
                    pool_info=models.PoolInformation(pool_id=pool_id),
                )
            )
        except Exception as exc:
            _log_batch_failure(
                "job.add",
                exc,
                pool_id=pool_id,
                job_id=job_id,
            )
            raise

        write_url = self._storage().create_transfer_url(
            container,
            "write",
            604800,
        )
        log_files = [
            models.OutputFile(
                file_pattern=f"$AZ_BATCH_TASK_DIR/{name}",
                destination=models.OutputFileDestination(
                    container=models.OutputFileBlobContainerDestination(
                        container_url=write_url,
                        path=posixpath.join(
                            data.get("log_prefix") or "",
                            f"azure_{name}",
                        ),
                    )
                ),
                upload_options=models.OutputFileUploadOptions(
                    upload_condition=(
                        models.OutputFileUploadCondition.task_completion
                    ),
                ),
            )
            for name in ("stderr.txt", "stdout.txt")
        ]

        tasks = []
        task_ids = []
        for index, command in enumerate(commands):
            specific_task_id = f"{task_id}-{index}"
            task_ids.append(specific_task_id)
            tasks.append(
                models.TaskAddParameter(
                    id=specific_task_id,
                    constraints=models.TaskConstraints(
                        max_wall_clock_time="PT16H",
                    ),
                    command_line=f'/bin/bash -c "{command}"',
                    resource_files=[],
                    output_files=(
                        log_files if index == len(commands) - 1 else []
                    ),
                    user_identity=models.UserIdentity(
                        auto_user=models.AutoUserSpecification(
                            elevation_level=models.ElevationLevel.admin,
                            scope=models.AutoUserScope.pool,
                        )
                    ),
                    environment_settings=[
                        models.EnvironmentSetting(
                            name="CONDA",
                            value="/usr/bin/miniconda/bin",
                        ),
                        *[
                            models.EnvironmentSetting(name=name, value=value)
                            for name, value in (
                                data.get("environment_variables") or {}
                            ).items()
                        ],
                    ],
                )
            )
        try:
            self.client.task.add_collection(job_id=job_id, value=tasks)
        except Exception as exc:
            _log_batch_failure(
                "task.add_collection",
                exc,
                pool_id=pool_id,
                job_id=job_id,
                task_count=len(tasks),
            )
            raise
        logger.info(
            "Azure Batch submission accepted pool_id=%s job_id=%s "
            "task_count=%s",
            pool_id,
            job_id,
            len(tasks),
        )
        return {
            "pool_id": pool_id,
            "job_id": job_id,
            "tasks": task_ids,
            "status": "Success",
            "error": "",
        }

    def _task(self, task):
        """Translate an Azure task object into provider-neutral data."""
        execution_info = _get(task, "execution_info")
        return {
            "id": _get(task, "id"),
            "state": _value(_get(task, "state")),
            "exit_code": _get(execution_info, "exit_code"),
        }

    def list_tasks(self, job_id):
        """List tasks belonging to a job.

        Args:
            job_id: Azure Batch job ID.

        Returns:
            Provider-neutral task records.

        """
        return [self._task(task) for task in self.client.task.list(job_id)]

    def get_task(self, job_id, task_id):
        """Get a task by job and task ID.

        Args:
            job_id: Azure Batch job ID.
            task_id: Azure Batch task ID.

        Returns:
            A provider-neutral task record.

        """
        return self._task(self.client.task.get(job_id, task_id))

    def list_task_files(self, job_id, task_id, recursive=False):
        """List files produced by a task.

        Args:
            job_id: Azure Batch job ID.
            task_id: Azure Batch task ID.
            recursive: Whether to include files in subdirectories.

        Returns:
            Provider-neutral file records.

        """
        files = self.client.file.list_from_task(
            job_id,
            task_id,
            recursive=recursive,
        )
        result = []
        for item in files:
            properties = _get(item, "properties")
            result.append(
                {
                    "name": _get(item, "name"),
                    "is_directory": bool(_get(item, "is_directory", False)),
                    "content_length": _get(
                        properties,
                        "content_length",
                        _get(item, "content_length"),
                    ),
                    "last_modified": _isoformat(
                        _get(
                            properties,
                            "last_modified",
                            _get(item, "last_modified"),
                        )
                    ),
                }
            )
        return result

    def download_task_file(self, job_id, task_id, file_path):
        """Download a task file using the Azure Batch client.

        Args:
            job_id: Azure Batch job ID.
            task_id: Azure Batch task ID.
            file_path: Path of the file within the task directory.

        Returns:
            The Azure SDK download result.

        """
        return self.client.file.get_from_task(job_id, task_id, file_path)

    def delete_job(self, job_id):
        """Delete a Batch job.

        Args:
            job_id: Azure Batch job ID.

        """
        return self.client.job.delete(job_id)

    def cancel_job(self, job_id):
        """Terminate a Batch job.

        Args:
            job_id: Azure Batch job ID.

        """
        return self.client.job.terminate(job_id)

    def list_pools(self):
        """List Batch pools as provider-neutral records."""
        pools = [self._pool(pool) for pool in self.client.pool.list()]
        for pool in pools:
            self._log_pool_status(pool)
        return pools

    def get_pool(self, pool_id):
        """Get a Batch pool as a provider-neutral record.

        Args:
            pool_id: Azure Batch pool ID.

        """
        pool = self._pool(self.client.pool.get(pool_id))
        self._log_pool_status(pool)
        return pool

    @staticmethod
    def _log_pool_status(pool):
        """Log pool allocation state and any Azure resize errors."""
        logger.info(
            "Azure Batch pool status pool_id=%s allocation_state=%s "
            "dedicated_nodes=%s/%s low_priority_nodes=%s/%s",
            pool["id"],
            pool["allocation_state"],
            pool["current_dedicated_nodes"],
            pool["target_dedicated_nodes"],
            pool["current_low_priority_nodes"],
            pool["target_low_priority_nodes"],
        )
        if pool["resize_errors"]:
            logger.warning(
                "Azure Batch pool resize/allocation errors "
                "pool_id=%s errors=%s",
                pool["id"],
                pool["resize_errors"],
            )

    @staticmethod
    def _pool(pool):
        """Translate an Azure pool object into provider-neutral data."""
        return {
            "id": _get(pool, "id"),
            "allocation_state": _value(_get(pool, "allocation_state")) or "",
            "current_dedicated_nodes": (
                _get(pool, "current_dedicated_nodes") or 0
            ),
            "target_dedicated_nodes": (
                _get(pool, "target_dedicated_nodes") or 0
            ),
            "current_low_priority_nodes": (
                _get(pool, "current_low_priority_nodes") or 0
            ),
            "target_low_priority_nodes": (
                _get(pool, "target_low_priority_nodes") or 0
            ),
            "resize_errors": [
                _resize_error(error)
                for error in (_get(pool, "resize_errors") or [])
            ],
        }

    def delete_pool(self, pool_id):
        """Delete a Batch pool.

        Args:
            pool_id: Azure Batch pool ID.

        """
        return self.client.pool.delete(pool_id)

    def resize_pool(
        self,
        pool_id,
        target_dedicated_nodes,
        target_low_priority_nodes,
    ):
        """Set a pool's target node counts.

        Args:
            pool_id: Azure Batch pool ID.
            target_dedicated_nodes: Desired dedicated node count.
            target_low_priority_nodes: Desired low-priority node count.

        """
        from azure.batch.models import PoolResizeParameter

        parameters = PoolResizeParameter(
            target_dedicated_nodes=target_dedicated_nodes,
            target_low_priority_nodes=target_low_priority_nodes,
        )
        return self.client.pool.resize(
            pool_id,
            pool_resize_parameter=parameters,
        )

    def list_nodes(self, pool_id):
        """List nodes in a Batch pool.

        Args:
            pool_id: Azure Batch pool ID.

        Returns:
            Node IDs and states.

        """
        return [
            {
                "id": _get(node, "id"),
                "state": _value(_get(node, "state")),
            }
            for node in self.client.compute_node.list(pool_id)
        ]

    def reboot_node(self, pool_id, node_id, option):
        """Reboot a node in a Batch pool.

        Args:
            pool_id: Azure Batch pool ID.
            node_id: Azure Batch compute node ID.
            option: Azure Batch reboot option.

        """
        return self.client.compute_node.reboot(
            pool_id,
            node_id,
            compute_node_reboot_option=option,
        )
