"""Provider-neutral HTTP client for the internal Batch service."""

from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import quote

import requests
from django.conf import settings


class BatchServiceError(RuntimeError):
    """Raised when the Batch service returns an unsuccessful response."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class BatchTask:
    id: str
    state: str
    execution_info: object


@dataclass(frozen=True)
class BatchTaskFile:
    name: str
    is_directory: bool
    content_length: int | None
    last_modified: str | None


@dataclass(frozen=True)
class BatchNode:
    id: str
    state: str


@dataclass(frozen=True)
class BatchResizeError:
    code: str
    message: str
    values: list


@dataclass(frozen=True)
class BatchPool:
    id: str
    allocation_state: str
    current_dedicated_nodes: int
    target_dedicated_nodes: int
    current_low_priority_nodes: int
    target_low_priority_nodes: int
    resize_errors: list


class _TaskOperations:
    def __init__(self, client):
        self.client = client

    def list(self, job_id):
        response = self.client._request(
            "GET",
            f"/v1/jobs/{quote(job_id, safe='')}/tasks",
        )
        return [
            BatchTask(
                id=item["id"],
                state=item["state"],
                execution_info=SimpleNamespace(exit_code=item.get("exit_code")),
            )
            for item in response.json()["items"]
        ]


class _JobOperations:
    def __init__(self, client):
        self.client = client

    def cancel(self, job_id):
        self.client._request(
            "POST",
            f"/v1/jobs/{quote(job_id, safe='')}/cancel",
        )

    def delete(self, job_id):
        self.client._request("DELETE", f"/v1/jobs/{quote(job_id, safe='')}")


class _PoolOperations:
    def __init__(self, client):
        self.client = client

    def list(self):
        response = self.client._request("GET", "/v1/pools")
        return [self.client._pool(item) for item in response.json()["items"]]

    def get(self, pool_id):
        response = self.client._request(
            "GET",
            f"/v1/pools/{quote(pool_id, safe='')}",
        )
        return self.client._pool(response.json())

    def exists(self, pool_id):
        try:
            self.get(pool_id)
        except BatchServiceError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    def delete(self, pool_id):
        self.client._request("DELETE", f"/v1/pools/{quote(pool_id, safe='')}")

    def resize(
        self,
        *,
        pool_id,
        target_dedicated_nodes,
        target_low_priority_nodes=0,
    ):
        self.client._request(
            "POST",
            f"/v1/pools/{quote(pool_id, safe='')}/resize",
            json={
                "target_dedicated_nodes": target_dedicated_nodes,
                "target_low_priority_nodes": target_low_priority_nodes,
            },
        )


class _FileOperations:
    def __init__(self, client):
        self.client = client

    def list_from_task(self, *, job_id, task_id, recursive=False):
        response = self.client._request(
            "GET",
            f"/v1/jobs/{quote(job_id, safe='')}/tasks/"
            f"{quote(task_id, safe='')}/files",
            params={"recursive": str(recursive).lower()},
        )
        return [
            BatchTaskFile(
                name=item["name"],
                is_directory=item.get("is_directory", False),
                content_length=item.get("content_length"),
                last_modified=item.get("last_modified"),
            )
            for item in response.json()["items"]
        ]

    def get_from_task(self, *, job_id, task_id, file_path):
        response = self.client._request(
            "GET",
            f"/v1/jobs/{quote(job_id, safe='')}/tasks/"
            f"{quote(task_id, safe='')}/files/{quote(file_path, safe='/')}",
            stream=True,
        )

        def chunks():
            try:
                yield from response.iter_content(chunk_size=64 * 1024)
            finally:
                response.close()

        return chunks()


class _ComputeNodeOperations:
    def __init__(self, client):
        self.client = client

    def list(self, pool_id):
        response = self.client._request(
            "GET",
            f"/v1/pools/{quote(pool_id, safe='')}/nodes",
        )
        return [
            BatchNode(id=item["id"], state=item["state"])
            for item in response.json()["items"]
        ]

    def reboot(self, *, pool_id, node_id, node_reboot_option="requeue"):
        self.client._request(
            "POST",
            f"/v1/pools/{quote(pool_id, safe='')}/nodes/"
            f"{quote(node_id, safe='')}/reboot",
            json={"option": node_reboot_option},
        )


class BatchServiceClient:
    """HTTP facade over provider-neutral Batch service operations."""

    def __init__(
        self,
        base_url=None,
        token=None,
        session=None,
        timeout=None,
    ):
        self.base_url = (
            base_url or settings.BATCH_SERVICE_BASE_URL
        ).rstrip("/")
        self.token = token or settings.BATCH_SERVICE_TOKEN
        self.session = session or requests.Session()
        self.timeout = timeout or settings.BATCH_REQUEST_TIMEOUT
        self.task = _TaskOperations(self)
        self.job = _JobOperations(self)
        self.pool = _PoolOperations(self)
        self.file = _FileOperations(self)
        self.compute_node = _ComputeNodeOperations(self)

    def _request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise BatchServiceError(
                None,
                f"Batch service request failed: {exc}",
            ) from exc
        if not response.ok:
            try:
                error = response.json()
                message = error.get("error", response.text)
            except ValueError:
                message = response.text
            raise BatchServiceError(response.status_code, message)
        return response

    @staticmethod
    def _pool(item):
        resize_errors = [
            BatchResizeError(
                code=error.get("code") or "",
                message=error.get("message") or "",
                values=[
                    SimpleNamespace(name=name, value=value)
                    for name, value in (error.get("details") or {}).items()
                ],
            )
            for error in item.get("resize_errors", [])
        ]
        return BatchPool(
            id=item["id"],
            allocation_state=item.get("allocation_state") or "",
            current_dedicated_nodes=item.get("current_dedicated_nodes") or 0,
            target_dedicated_nodes=item.get("target_dedicated_nodes") or 0,
            current_low_priority_nodes=item.get("current_low_priority_nodes") or 0,
            target_low_priority_nodes=item.get("target_low_priority_nodes") or 0,
            resize_errors=resize_errors,
        )
