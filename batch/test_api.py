"""Focused validation tests for the internal Batch submission API."""

import hashlib
import hmac
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from provider import (
    AzureBatchProvider,
    StorageServiceClient,
    SubmissionError,
)

API_PATH = Path(__file__).with_name("api.py")
SPEC = importlib.util.spec_from_file_location("batch_api", str(API_PATH))
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def valid_payload():
    return {
        "analysis_type": "Nanopore",
        "command_file": ["foodport-nanopore-task --manifest '$MANIFEST'"],
        "container": "nanopore-runs",
        "download_file_pattern": [],
        "input_file_pattern": [
            "nanopore-runs/42/input/manifest.json",
            "nanopore-runs/42/input/pod5/",
        ],
        "vm_size": "Standard_NV18ads_A10_v5",
        "output_file_pattern": ["output/"],
        "output_container": "nanopore-results",
        "output_prefix": "runs/260825-nanopore",
        "unique_id": "foodport-nanopore-42",
        "log_prefix": "260825-nanopore/logs",
    }


def test_nanopore_payload_normalizes_patterns():
    payload = api._validate_payload(valid_payload())
    assert payload["analysis_type"] == "Nanopore"
    assert payload["input_patterns"] == [
        "nanopore-runs/42/input/manifest.json",
        "nanopore-runs/42/input/pod5/",
    ]
    assert payload["download_patterns"] == []
    assert payload["output_patterns"] == ["output/"]
    assert payload["output_container"] == "nanopore-results"
    assert payload["output_prefix"] == "runs/260825-nanopore"


def test_nanopore_payload_rejects_scalar_input_pattern():
    payload = valid_payload()
    payload["input_file_pattern"] = "nanopore-runs/42/input/**"
    with pytest.raises(TypeError):
        api._validate_payload(payload)


def test_submission_validates_environment_variables():
    payload = valid_payload()
    payload["environment_variables"] = {
        "FOODPORT_INPUT_SAS_URL": "https://storage.example/input?sig=token",
    }
    api._validate_payload(payload)

    payload["environment_variables"] = ["not", "a", "mapping"]
    with pytest.raises(TypeError, match="environment_variables"):
        api._validate_payload(payload)


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.fail_with = None
        self.submissions = []

    def submit_batch_request(self, data):
        self.submissions.append(data)
        return {
            "pool_id": "pool-1",
            "job_id": "job-1",
            "tasks": ["task-1"],
            "status": "Success",
            "error": "",
        }

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.fail_with:
            raise self.fail_with

    def list_tasks(self, job_id):
        self._record("list_tasks", job_id)
        return [{"id": "task-1", "state": "completed", "exit_code": 0}]

    def get_task(self, job_id, task_id):
        self._record("get_task", job_id, task_id)
        return {"id": task_id, "state": "running", "exit_code": None}

    def list_task_files(self, job_id, task_id, recursive=False):
        self._record("list_task_files", job_id, task_id, recursive=recursive)
        return [
            {
                "name": "stdout.txt",
                "is_directory": False,
                "content_length": 12,
                "last_modified": "2026-11-06T12:00:00+00:00",
            }
        ]

    def download_task_file(self, job_id, task_id, file_path):
        self._record("download_task_file", job_id, task_id, file_path)
        return b"raw-file-bytes"

    def delete_job(self, job_id):
        self._record("delete_job", job_id)

    def cancel_job(self, job_id):
        self._record("cancel_job", job_id)

    def list_pools(self):
        self._record("list_pools")
        return [self.pool()]

    def get_pool(self, pool_id):
        self._record("get_pool", pool_id)
        return self.pool(pool_id)

    @staticmethod
    def pool(pool_id="pool-1"):
        return {
            "id": pool_id,
            "allocation_state": "steady",
            "current_dedicated_nodes": 2,
            "target_dedicated_nodes": 2,
            "current_low_priority_nodes": 1,
            "target_low_priority_nodes": 1,
            "resize_errors": [
                {
                    "code": "AllocationFailed",
                    "message": "A node could not be allocated",
                    "details": {"node": "node-1"},
                }
            ],
        }

    def delete_pool(self, pool_id):
        self._record("delete_pool", pool_id)

    def resize_pool(
        self,
        pool_id,
        target_dedicated_nodes,
        target_low_priority_nodes,
    ):
        self._record(
            "resize_pool",
            pool_id,
            target_dedicated_nodes,
            target_low_priority_nodes,
        )

    def list_nodes(self, pool_id):
        self._record("list_nodes", pool_id)
        return [{"id": "node-1", "state": "idle"}]

    def reboot_node(self, pool_id, node_id, option):
        self._record("reboot_node", pool_id, node_id, option)


@pytest.fixture
def service(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setitem(api.app.config, "SECRET_KEY", "test-secret")
    monkeypatch.setitem(api.app.config, "BATCH_SERVICE_TOKEN", None)
    monkeypatch.setitem(api.app.config, "BATCH_PROVIDER", provider)
    return api.app.test_client(), provider


def auth_headers():
    token = hmac.new(
        b"test-secret",
        b"foodport-batch-service",
        hashlib.sha256,
    ).hexdigest()
    return {"Authorization": f"Bearer {token}"}


def test_health_is_unauthenticated(service):
    client, _ = service
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}


@pytest.mark.parametrize(
    "path,method",
    [
        ("/", "get"),
        ("/v1/jobs/job-1/tasks", "get"),
        ("/v1/jobs/job-1/cancel", "post"),
        ("/v1/pools", "get"),
        ("/submit_batch_request", "post"),
    ],
)
def test_operations_require_bearer_auth(service, path, method):
    client, _ = service
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json == {"error": "Authentication required"}


def test_task_contracts(service):
    client, _ = service
    headers = auth_headers()
    response = client.get("/v1/jobs/job-1/tasks", headers=headers)
    assert response.status_code == 200
    assert response.json == {
        "items": [{"id": "task-1", "state": "completed", "exit_code": 0}]
    }

    response = client.get(
        "/v1/jobs/job-1/tasks/task-2",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json == {
        "id": "task-2",
        "state": "running",
        "exit_code": None,
    }


def test_task_file_listing_and_download_contracts(service):
    client, provider = service
    headers = auth_headers()
    response = client.get(
        "/v1/jobs/job-1/tasks/task-1/files?recursive=true",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json == {
        "items": [
            {
                "name": "stdout.txt",
                "is_directory": False,
                "content_length": 12,
                "last_modified": "2026-11-06T12:00:00+00:00",
            }
        ]
    }
    assert provider.calls[-1] == (
        "list_task_files",
        ("job-1", "task-1"),
        {"recursive": True},
    )

    response = client.get(
        "/v1/jobs/job-1/tasks/task-1/files/subdir/stdout.txt",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.data == b"raw-file-bytes"
    assert response.mimetype == "application/octet-stream"


def test_pool_and_node_contracts(service):
    client, _ = service
    headers = auth_headers()
    response = client.get("/v1/pools", headers=headers)
    assert response.status_code == 200
    assert response.json == {"items": [FakeProvider.pool()]}

    response = client.get("/v1/pools/pool-2", headers=headers)
    assert response.status_code == 200
    assert response.json == FakeProvider.pool("pool-2")

    response = client.get("/v1/pools/pool-1/nodes", headers=headers)
    assert response.status_code == 200
    assert response.json == {"items": [{"id": "node-1", "state": "idle"}]}


def test_pool_resize_reboot_and_delete_contracts(service):
    client, provider = service
    headers = auth_headers()
    response = client.post(
        "/v1/pools/pool-1/resize",
        headers=headers,
        json={
            "target_dedicated_nodes": 3,
            "target_low_priority_nodes": 4,
        },
    )
    assert response.status_code == 202
    assert provider.calls[-1] == (
        "resize_pool",
        ("pool-1", 3, 4),
        {},
    )

    response = client.post(
        "/v1/pools/pool-1/nodes/node-1/reboot",
        headers=headers,
        json={"option": "requeue"},
    )
    assert response.status_code == 202
    assert provider.calls[-1] == (
        "reboot_node",
        ("pool-1", "node-1", "requeue"),
        {},
    )

    response = client.delete("/v1/jobs/job-1", headers=headers)
    assert response.status_code == 200
    assert response.json == {"id": "job-1", "status": "deleted"}
    response = client.delete("/v1/pools/pool-1", headers=headers)
    assert response.status_code == 200
    assert response.json == {"id": "pool-1", "status": "deleted"}


def test_cancel_is_distinct_from_delete(service):
    client, provider = service
    response = client.post(
        "/v1/jobs/job-1/cancel",
        headers=auth_headers(),
    )
    assert response.status_code == 202
    assert response.json == {
        "id": "job-1",
        "status": "cancellation_requested",
    }
    assert provider.calls == [("cancel_job", ("job-1",), {})]


def test_provider_not_found_is_returned_as_json_404(service):
    client, provider = service

    class NotFoundError(Exception):
        status_code = 404
        code = "TaskNotFound"

    provider.fail_with = NotFoundError("task does not exist")
    response = client.get(
        "/v1/jobs/job-1/tasks/task-1",
        headers=auth_headers(),
    )
    assert response.status_code == 404
    assert response.json == {
        "error": "task does not exist",
        "code": "TaskNotFound",
    }


def test_provider_error_models_are_serialized_as_json(service):
    client, provider = service
    provider.fail_with = RuntimeError("Azure Batch request failed")
    provider.fail_with.error = SimpleNamespace(
        code="JobNotFound",
        message=SimpleNamespace(value="The specified job does not exist."),
        details=[
            SimpleNamespace(
                key=SimpleNamespace(value="request"),
                value=SimpleNamespace(value="job lookup"),
            )
        ],
    )

    response = client.get(
        "/v1/jobs/missing-job/tasks",
        headers=auth_headers(),
    )

    assert response.status_code == 404
    assert response.json == {
        "error": "The specified job does not exist.",
        "code": "JobNotFound",
        "details": [{"key": "request", "value": "job lookup"}],
    }


def test_resize_rejects_invalid_counts(service):
    client, _ = service
    response = client.post(
        "/v1/pools/pool-1/resize",
        headers=auth_headers(),
        json={"target_dedicated_nodes": -1},
    )
    assert response.status_code == 400
    assert "target_dedicated_nodes" in response.json["error"]


def test_submission_keeps_foodport_request_and_response_contract(service):
    client, provider = service
    payload = valid_payload()
    payload["input_file_pattern"] = [
        ["nanopore-runs/42/input/manifest.json"],
        ["nanopore-runs/42/input/pod5/", "pod5"],
    ]
    response = client.post(
        "/submit_batch_request",
        headers=auth_headers(),
        json=payload,
    )
    assert response.status_code == 200
    assert response.json == {
        "pool_id": "pool-1",
        "job_id": "job-1",
        "tasks": ["task-1"],
        "status": "Success",
        "error": "",
    }
    assert provider.submissions == [payload]


class FakeStorageService:
    def __init__(self):
        self.calls = []

    def ensure_container(self, container):
        self.calls.append(("ensure_container", container))

    def list_objects(self, container, prefix=None):
        self.calls.append(("list_objects", container, prefix))
        if container == "target-runs":
            return [{"name": "preexisting.fasta"}]
        return [
            {"name": "reads/one.fasta"},
            {"name": "reads/nested/two.fasta"},
            {"name": "reads/skip.txt"},
        ]

    def copy_object(
        self, source_container, source_name, dest_container, dest_name
    ):
        self.calls.append(
            (
                "copy_object",
                source_container,
                source_name,
                dest_container,
                dest_name,
            )
        )

    def create_transfer_url(self, container, action, expires_in_seconds):
        self.calls.append(
            ("create_transfer_url", container, action, expires_in_seconds)
        )
        return f"https://storage.example/{container}?sv=test&sig={action}"


class FakeBatchOperations:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def add(self, value):
        self.calls.append(("add", value))

    def add_collection(self, **kwargs):
        self.calls.append(("add_collection", kwargs))


class FakeBatchClient:
    def __init__(self):
        self.pool = FakeBatchOperations("pool")
        self.job = FakeBatchOperations("job")
        self.task = FakeBatchOperations("task")


def configure_submission(monkeypatch):
    for key, value in {
        "VM_IMAGE": (
            "/subscriptions/test/resourceGroups/images/providers/"
            "Microsoft.Compute/galleries/gallery/images/cowbat/versions/1.0.0"
        ),
        "COWBAT_NODE_AGENT_SKU": "batch.node.ubuntu 22.04",
        "BATCH_ACCOUNT_SUBNET": "/subscriptions/test/subnets/batch",
        "AZURE_ACCOUNT_NAME": "storageaccount",
    }.items():
        monkeypatch.setenv(key, value)


def test_create_client_uses_managed_identity(monkeypatch):
    import azure.batch
    import msrestazure.azure_active_directory

    calls = {}

    class FakeManagedIdentityAuthentication:
        def __init__(self, **kwargs):
            calls["credential"] = kwargs

    class FakeBatchServiceClient:
        def __init__(self, credentials, endpoint):
            self.credentials = credentials
            self.endpoint = endpoint

    monkeypatch.setattr(
        msrestazure.azure_active_directory,
        "MSIAuthentication",
        FakeManagedIdentityAuthentication,
    )
    monkeypatch.setattr(
        azure.batch,
        "BatchServiceClient",
        FakeBatchServiceClient,
    )
    monkeypatch.setenv(
        "BATCH_ACCOUNT_URL",
        "https://batch.example.test",
    )
    for key in (
        "BATCH_ACCOUNT_NAME",
        "BATCH_ACCOUNT_KEY",
        "AZURE_BATCH_ACCOUNT_NAME",
        "AZURE_BATCH_ACCOUNT_KEY",
        "AZURE_BATCH_ACCOUNT",
        "AZURE_BATCH_ACCESS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    client = AzureBatchProvider._create_client()

    assert calls["credential"] == {
        "resource": "https://batch.core.windows.net/",
    }
    assert isinstance(client, FakeBatchServiceClient)
    assert isinstance(client.credentials, FakeManagedIdentityAuthentication)
    assert client.endpoint == "https://batch.example.test"


def test_submission_translates_pool_tasks_and_delegates_blob_work(monkeypatch):
    configure_submission(monkeypatch)
    batch_client = FakeBatchClient()
    storage = FakeStorageService()
    provider = AzureBatchProvider(client=batch_client, storage_client=storage)
    result = provider.submit_batch_request(
        {
            "analysis_type": "COWBAT",
            "command_file": ["echo first", "echo second"],
            "container": "target-runs",
            "download_file_pattern": [],
            "input_file_pattern": [
                ["source-runs/reads/*.fasta", "imports"],
                ["target-runs/preexisting.fasta"],
            ],
            "vm_size": "Standard_D4s_v3",
            "unique_id": "FoodPort",
            "environment_variables": {
                "FOODPORT_INPUT_SAS_URL": "https://storage.example/input?sig=token",
            },
        }
    )

    assert result == {
        "pool_id": "target-runs",
        "job_id": "target-runs",
        "tasks": ["target-runs-0", "target-runs-1"],
        "status": "Success",
        "error": "",
    }
    assert storage.calls == [
        ("ensure_container", "target-runs"),
        ("list_objects", "source-runs", "reads"),
        (
            "copy_object",
            "source-runs",
            "reads/one.fasta",
            "target-runs",
            "imports/one.fasta",
        ),
        (
            "copy_object",
            "source-runs",
            "reads/nested/two.fasta",
            "target-runs",
            "imports/two.fasta",
        ),
        ("list_objects", "target-runs", "preexisting.fasta"),
        ("create_transfer_url", "target-runs", "readwrite", 604800),
        ("create_transfer_url", "target-runs", "write", 604800),
    ]
    pool = batch_client.pool.calls[0][1]
    blob_mount = pool.mount_configuration[
        0
    ].azure_blob_file_system_configuration
    assert blob_mount.account_key is None
    assert blob_mount.sas_key == "sv=test&sig=readwrite"
    assert batch_client.job.calls[0][1].id == "target-runs"
    tasks = batch_client.task.calls[0][1]["value"]
    assert [task.command_line for task in tasks] == [
        '/bin/bash -c "echo first"',
        '/bin/bash -c "echo second"',
    ]
    assert {
        item.name: item.value for item in tasks[0].environment_settings
    } == {
        "CONDA": "/usr/bin/miniconda/bin",
        "FOODPORT_INPUT_SAS_URL": ("https://storage.example/input?sig=token"),
    }
    assert tasks[0].output_files == []
    assert [output.file_pattern for output in tasks[1].output_files] == [
        "$AZ_BATCH_TASK_DIR/stderr.txt",
        "$AZ_BATCH_TASK_DIR/stdout.txt",
    ]


def test_submission_rejects_unsupported_analysis_type_without_blob_calls():
    batch_client = FakeBatchClient()
    storage = FakeStorageService()
    provider = AzureBatchProvider(client=batch_client, storage_client=storage)
    with pytest.raises(SubmissionError, match="Unsupported analysis_type"):
        provider.submit_batch_request(
            {
                "analysis_type": "Nanopore",
                "command_file": "echo run",
                "container": "target-runs",
                "input_file_pattern": None,
                "vm_size": "Standard_D4s_v3",
            }
        )
    assert storage.calls == []


def test_submission_rejects_no_tidy_for_async_requests():
    provider = AzureBatchProvider(
        client=FakeBatchClient(),
        storage_client=FakeStorageService(),
    )
    with pytest.raises(SubmissionError, match="no_tidy is not supported"):
        provider.submit_batch_request({"no_tidy": True})


def test_storage_client_delegates_to_storage_http_contract():
    session = Mock()
    listed = Mock(ok=True)
    listed.json.return_value = {"items": [{"name": "reads/one.fasta"}]}
    transfer = Mock(ok=True)
    transfer.json.return_value = {"url": "https://storage.example/target?sas"}
    session.request.side_effect = [
        Mock(ok=True),
        listed,
        Mock(ok=True),
        transfer,
    ]
    storage = StorageServiceClient(
        base_url="http://storage:5001",
        token="storage-token",
        session=session,
    )

    storage.ensure_container("target-runs")
    assert storage.list_objects("source-runs", "reads") == [
        {"name": "reads/one.fasta"}
    ]
    storage.copy_object(
        "source-runs",
        "reads/one.fasta",
        "target-runs",
        "imports/one.fasta",
    )
    assert (
        storage.create_transfer_url(
            "target-runs",
            "readwrite",
            604800,
        )
        == "https://storage.example/target?sas"
    )

    calls = session.request.call_args_list
    assert [(item.args[0], item.args[1]) for item in calls] == [
        ("PUT", "http://storage:5001/v1/containers/target-runs"),
        ("GET", "http://storage:5001/v1/containers/source-runs/objects"),
        ("POST", "http://storage:5001/v1/objects/copy"),
        ("POST", "http://storage:5001/v1/transfer-urls"),
    ]
    assert all(
        item.kwargs["headers"]["Authorization"].startswith("Bearer ")
        and item.kwargs["timeout"] == 600
        for item in calls
    )
    assert calls[1].kwargs["params"] == {"prefix": "reads"}
    assert calls[2].kwargs["json"] == {
        "source_container": "source-runs",
        "source_name": "reads/one.fasta",
        "destination_container": "target-runs",
        "destination_name": "imports/one.fasta",
    }
    assert calls[3].kwargs["json"] == {
        "container": "target-runs",
        "action": "readwrite",
        "expires_in_seconds": 604800,
    }
