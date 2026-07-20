#!/usr/bin/env python3

"""Flask API for the RedmineAssistant retrieval service."""
# Standard library imports
import logging
import time
from typing import Any
from uuid import uuid4

# Third-party imports
from flask import Flask, g, jsonify, request

# Local imports
from config import ConfigurationError, settings
from database import (
    check_database_connection,
    get_database_status,
)
from retrieval import (
    RetrievalError,
    SearchResult,
    retrieve_chunks,
    validate_limit,
    validate_query,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

LOGGER = logging.getLogger("redmine-assistant-api")

app = Flask(__name__)


PROHIBITED_ACCESS_FIELDS = frozenset(
    {
        "include_internal",
        "access_context",
    }
)


@app.before_request
def assign_request_id() -> None:
    """Assign a locally generated identifier to each HTTP request."""
    g.request_id = str(uuid4())
    g.request_started = time.monotonic()


@app.after_request
def add_request_id_header(response):
    """Return the request identifier as a response header."""
    request_id = getattr(
        g,
        "request_id",
        None,
    )

    if request_id:
        response.headers["X-Request-ID"] = request_id

    return response


def current_request_id() -> str:
    """Return the identifier assigned to the current request."""
    return str(
        getattr(
            g,
            "request_id",
            "unknown",
        )
    )


def error_response(
    code: str,
    message: str,
    status_code: int,
):
    """
    Create a consistent JSON error response.

    Args:
        code: Stable machine-readable error code.
        message: User-facing error description.
        status_code: HTTP response status.

    Returns:
        Flask JSON response and HTTP status code.
    """
    return jsonify(
        {
            "status": "error",
            "request_id": current_request_id(),
            "error": {
                "code": code,
                "message": message,
            },
        }
    ), status_code


def serialize_search_result(
    result: SearchResult,
) -> dict[str, Any]:
    """
    Convert one semantic-search result into an API response value.

    Args:
        result: Retrieved documentation chunk.

    Returns:
        JSON-serializable result mapping.
    """
    return {
        "rank": result.rank,
        "score": result.score,
        "chunk_key": result.chunk_key,
        "source_path": result.source_path,
        "source_url": result.source_url,
        "document_title": result.document_title,
        "heading_path": result.heading_path,
        "content": result.content,
        "access_level": result.access_level,
    }


def bounded_excerpt(
    content: str,
) -> str:
    """
    Return a bounded excerpt for the public retrieval response.

    Whitespace is trimmed and oversized content is truncated near a word
    boundary when possible.
    """
    normalized = content.strip()
    maximum = settings.max_excerpt_chars

    if len(normalized) <= maximum:
        return normalized

    candidate = normalized[: maximum + 1]
    boundary = candidate.rfind(" ")

    if boundary >= max(1, maximum // 2):
        candidate = candidate[:boundary]
    else:
        candidate = candidate[:maximum]

    return candidate.rstrip() + "…"


def serialize_retrieval_source(
    result: SearchResult,
) -> dict[str, Any]:
    """
    Convert one search result into a bounded v1 source object.
    """
    return {
        "rank": result.rank,
        "score": result.score,
        "chunk_key": result.chunk_key,
        "source_path": result.source_path,
        "source_url": result.source_url,
        "document_title": result.document_title,
        "heading_path": result.heading_path,
        "excerpt": bounded_excerpt(result.content),
        "access_level": result.access_level,
    }


def standard_results(
    results: list[SearchResult],
) -> list[SearchResult]:
    """
    Defensively remove internal material from a standard response.
    """
    filtered = [
        result
        for result in results
        if (
            result.access_level == "standard"
            and not result.source_path.startswith(
                "internal_only/"
            )
        )
    ]

    if len(filtered) != len(results):
        LOGGER.error(
            "request_id=%s retrieval returned internal "
            "documentation to a standard endpoint",
            current_request_id(),
        )

    return filtered


@app.get("/health")
def health():
    """
    Return process liveness.

    This endpoint deliberately does not check external dependencies.
    """
    return jsonify(
        {
            "status": "ok",
            "service": "redmine-assistant-rag",
        }
    )


@app.get("/health/ready")
def readiness():
    """
    Return application readiness.

    The service is considered ready when PostgreSQL is reachable and the
    database schema can be inspected.
    """
    try:
        check_database_connection()
        database_status = get_database_status()
    except Exception:
        LOGGER.exception("RAG service readiness check failed")

        return jsonify(
            {
                "status": "not_ready",
                "database": "unavailable",
            }
        ), 503

    return jsonify(
        {
            "status": "ready",
            "database": "connected",
            "schema_migrations": database_status["migration_count"],
            "documents": database_status["documents"],
            "chunks": database_status["chunks"],
        }
    )


@app.post("/search")
def search():
    """
    Search standard-access RedmineAssistant documentation.

    The HTTP API deliberately excludes internal-only documentation.
    Internal-document access is not accepted as a request option and must
    remain behind a separately authenticated and authorized integration.

    Expected JSON body:

    {
        "query": "Which automator detects plasmids?",
        "limit": 5
    }
    """
    if not request.is_json:
        return error_response(
            code="unsupported_media_type",
            message=("The request Content-Type must be application/json."),
            status_code=415,
        )

    request_body = request.get_json(silent=True)

    if request_body is None:
        return error_response(
            code="invalid_json",
            message="The request body must contain valid JSON.",
            status_code=400,
        )

    if not isinstance(request_body, dict):
        return error_response(
            code="invalid_request",
            message="The JSON request body must be an object.",
            status_code=400,
        )

    if "query" not in request_body:
        return error_response(
            code="missing_query",
            message="The request body must include a query.",
            status_code=400,
        )

    try:
        query = validate_query(request_body["query"])
        limit = validate_limit(request_body.get("limit"))
    except RetrievalError as exc:
        return error_response(
            code="invalid_request",
            message=str(exc),
            status_code=400,
        )

    LOGGER.info(
        "Semantic search request received: query_length=%s, limit=%s",
        len(query),
        limit,
    )

    try:
        results = retrieve_chunks(
            query=query,
            limit=limit,
            include_internal=False,
        )
    except RetrievalError:
        LOGGER.exception("Semantic search could not be completed")

        return error_response(
            code="search_unavailable",
            message=("The documentation search service is temporarily unavailable."),
            status_code=503,
        )
    except Exception:
        LOGGER.exception("Unexpected semantic-search API failure")

        return error_response(
            code="internal_error",
            message=("An unexpected error occurred while searching the documentation."),
            status_code=500,
        )

    standard_results = [
        result
        for result in results
        if (
            result.access_level == "standard"
            and not result.source_path.startswith("internal_only/")
        )
    ]

    if len(standard_results) != len(results):
        LOGGER.error(
            "Retrieval returned internal documentation to the "
            "standard-access search endpoint"
        )

    return jsonify(
        {
            "status": "ok",
            "query": query,
            "count": len(standard_results),
            "limit": limit,
            "results": [serialize_search_result(result) for result in standard_results],
        }
    )


@app.post("/api/v1/retrieve")
def retrieve_v1():
    """
    Return structured standard-access retrieval evidence.

    This endpoint does not accept client-selected access context.
    Internal access will be added later through a trusted server
    integration.
    """
    if not request.is_json:
        return error_response(
            code="unsupported_media_type",
            message=("The request Content-Type must be application/json."),
            status_code=415,
        )

    request_body = request.get_json(
        silent=True,
    )

    if request_body is None:
        return error_response(
            code="invalid_json",
            message=("The request body must contain valid JSON."),
            status_code=400,
        )

    if not isinstance(request_body, dict):
        return error_response(
            code="invalid_request",
            message=("The JSON request body must be an object."),
            status_code=400,
        )

    prohibited_fields = sorted(PROHIBITED_ACCESS_FIELDS.intersection(request_body))

    if prohibited_fields:
        return error_response(
            code="forbidden_access_context",
            message=("Access context is assigned by a trusted server integration."),
            status_code=400,
        )

    if "query" not in request_body:
        return error_response(
            code="missing_query",
            message=("The request body must include a query."),
            status_code=400,
        )

    try:
        query = validate_query(request_body["query"])
        limit = validate_limit(request_body.get("limit"))
    except RetrievalError as exc:
        return error_response(
            code="invalid_request",
            message=str(exc),
            status_code=400,
        )

    started = time.monotonic()

    try:
        results = retrieve_chunks(
            query=query,
            limit=limit,
            include_internal=False,
        )
    except RetrievalError:
        LOGGER.exception(
            "request_id=%s semantic retrieval unavailable",
            current_request_id(),
        )

        return error_response(
            code="search_unavailable",
            message=("The documentation search service is temporarily unavailable."),
            status_code=503,
        )
    except Exception:
        LOGGER.exception(
            "request_id=%s unexpected retrieval failure",
            current_request_id(),
        )

        return error_response(
            code="internal_error",
            message=("An unexpected error occurred while searching the documentation."),
            status_code=500,
        )

    results = standard_results(results)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    LOGGER.info(
        "request_id=%s access_context=standard "
        "query_length=%s limit=%s results=%s "
        "elapsed_ms=%.1f",
        current_request_id(),
        len(query),
        limit,
        len(results),
        elapsed_ms,
    )

    sources = [serialize_retrieval_source(result) for result in results]

    return jsonify(
        {
            "status": "ok",
            "request_id": current_request_id(),
            "query": query,
            "answer_mode": "retrieval_only",
            "answer": None,
            "abstained": False,
            "access_context": "standard",
            "result_count": len(sources),
            "limit": limit,
            "sources": sources,
        }
    )


@app.errorhandler(ConfigurationError)
def configuration_error(error):
    """Return a generic response for application configuration failures."""
    LOGGER.error("Application configuration error: %s", error)

    return error_response(
        code="configuration_error",
        message="Application configuration is invalid.",
        status_code=500,
    )
