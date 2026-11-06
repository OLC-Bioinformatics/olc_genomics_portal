"""Internal HTTP API for provider-neutral object storage operations."""

import hmac
import os
import re

from flask import Flask, Response, jsonify, request

from provider import AzureStorageProvider, StorageNotFoundError


CONTAINER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
ALLOWED_ACTIONS = {"read", "write", "readwrite"}


def _validate_name(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string.")
    if value.startswith("/") or "\\" in value or any(
        part in {".", ".."} for part in value.split("/")
    ):
        raise ValueError(f"Invalid {label}.")


def create_app(provider=None, token=None):
    app = Flask(__name__)
    if provider is None:
        backend = os.environ.get("STORAGE_BACKEND", "azure").lower()
        if backend != "azure":
            raise RuntimeError(f"Unsupported storage backend: {backend}")
        provider = AzureStorageProvider(
            account_name=os.environ.get("AZURE_ACCOUNT_NAME"),
            account_key=os.environ.get("AZURE_ACCOUNT_KEY"),
        )
    app.config["STORAGE_PROVIDER"] = provider
    expected_token = token or os.environ.get("STORAGE_SERVICE_TOKEN")
    if not expected_token:
        # A domain-separated token derived from the app's existing secret avoids
        # adding a second secret to the shared Dev environment file.
        secret_key = os.environ.get("SECRET_KEY")
        if secret_key:
            import hashlib

            expected_token = hmac.new(
                secret_key.encode("utf-8"),
                b"foodport-storage-service",
                hashlib.sha256,
            ).hexdigest()
    if not expected_token:
        raise RuntimeError("Storage service authentication is not configured.")
    app.config["STORAGE_SERVICE_TOKEN"] = expected_token

    @app.errorhandler(StorageNotFoundError)
    def not_found(error):
        return jsonify({"error": str(error)}), 404

    @app.errorhandler(ValueError)
    def invalid_request(error):
        return jsonify({"error": str(error)}), 400

    @app.before_request
    def authenticate():
        if request.endpoint == "health":
            return None
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {app.config['STORAGE_SERVICE_TOKEN']}"
        if not hmac.compare_digest(supplied, expected):
            return jsonify({"error": "Unauthorized"}), 401
        return None

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/v1/containers")
    def list_containers():
        return jsonify({"items": app.config["STORAGE_PROVIDER"].list_containers()})

    @app.put("/v1/containers/<container>")
    def ensure_container(container):
        if not CONTAINER_PATTERN.fullmatch(container):
            raise ValueError("Invalid container name.")
        app.config["STORAGE_PROVIDER"].ensure_container(container)
        return "", 204

    @app.delete("/v1/containers/<container>")
    def delete_container(container):
        if not CONTAINER_PATTERN.fullmatch(container):
            raise ValueError("Invalid container name.")
        app.config["STORAGE_PROVIDER"].delete_container(container)
        return "", 204

    @app.post("/v1/containers/rename")
    def rename_container():
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise ValueError("A JSON request body is required.")
        old_name = payload.get("old_name")
        new_name = payload.get("new_name")
        if not all(
            isinstance(name, str) and CONTAINER_PATTERN.fullmatch(name)
            for name in (old_name, new_name)
        ):
            raise ValueError("Invalid source or destination container name.")
        app.config["STORAGE_PROVIDER"].rename_container(old_name, new_name)
        return "", 204

    @app.get("/v1/containers/<container>/objects")
    def list_objects(container):
        if not CONTAINER_PATTERN.fullmatch(container):
            raise ValueError("Invalid container name.")
        prefix = request.args.get("prefix")
        if prefix:
            _validate_name(prefix, "prefix")
        items = app.config["STORAGE_PROVIDER"].list_objects(container, prefix)
        return jsonify({"items": items})

    @app.route(
        "/v1/containers/<container>/objects/<path:name>",
        methods=["GET", "HEAD", "PUT", "DELETE"],
    )
    def object_resource(container, name):
        if not CONTAINER_PATTERN.fullmatch(container):
            raise ValueError("Invalid container name.")
        _validate_name(name, "object name")
        storage = app.config["STORAGE_PROVIDER"]
        if request.method == "PUT":
            record = storage.upload_object(
                container=container,
                name=name,
                data=request.stream,
                content_type=request.headers.get("Content-Type"),
                content_md5=request.headers.get("Content-MD5"),
            )
            return jsonify(record), 201
        if request.method == "DELETE":
            storage.delete_object(container, name)
            return "", 204
        if request.method == "HEAD":
            record = storage.get_object(container, name)
            return "", 200, {
                "Content-Length": str(record.get("size") or 0),
                "Last-Modified": record.get("last_modified") or "",
                "ETag": record.get("etag") or "",
                "Content-Type": record.get("content_type") or "application/octet-stream",
                "Content-MD5": record.get("content_md5") or "",
            }
        properties, chunks = storage.download_object(container, name)
        content_type = getattr(
            getattr(properties, "content_settings", None),
            "content_type",
            None,
        )
        return Response(
            chunks,
            mimetype=content_type or "application/octet-stream",
        )

    @app.post("/v1/objects/copy")
    def copy_object():
        payload = request.get_json()
        required = (
            "source_container",
            "source_name",
            "destination_container",
            "destination_name",
        )
        if not isinstance(payload, dict) or any(
            not payload.get(key) for key in required
        ):
            raise ValueError("Copy request is missing required object fields.")
        for key in ("source_name", "destination_name"):
            _validate_name(payload[key], key)
        for key in ("source_container", "destination_container"):
            if not isinstance(payload[key], str) or not CONTAINER_PATTERN.fullmatch(
                payload[key]
            ):
                raise ValueError(f"Invalid {key}.")
        record = app.config["STORAGE_PROVIDER"].copy_object(
            payload["source_container"],
            payload["source_name"],
            payload["destination_container"],
            payload["destination_name"],
        )
        return jsonify(record)

    @app.post("/v1/transfer-urls")
    def create_transfer_url():
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise ValueError("A JSON request body is required.")
        container = payload.get("container")
        if not isinstance(container, str) or not CONTAINER_PATTERN.fullmatch(container):
            raise ValueError("Invalid container name.")
        action = payload.get("action")
        if action not in ALLOWED_ACTIONS:
            raise ValueError(
                "Action must be 'read', 'write', or 'readwrite'."
            )
        expires = payload.get("expires_in_seconds", 3600)
        if (
            isinstance(expires, bool)
            or not isinstance(expires, int)
            or not 1 <= expires <= 604800
        ):
            raise ValueError("Transfer URL expiry must be between 1 and 604800 seconds.")
        name = payload.get("name")
        if name is not None:
            _validate_name(name, "object name")
        url = app.config["STORAGE_PROVIDER"].create_transfer_url(
            container=container,
            name=name,
            action=action,
            expires_in_seconds=expires,
            content_disposition=payload.get("content_disposition"),
        )
        return jsonify({"url": url})

    return app


app = create_app()
