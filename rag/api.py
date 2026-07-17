#!/usr/bin/env python3

"""Flask API for the RedmineAssistant retrieval service."""

import logging
from typing import Any

from flask import Flask, jsonify, request

from config import ConfigurationError
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


@app.errorhandler(ConfigurationError)
def configuration_error(error):
    """Return a generic response for application configuration failures."""
    LOGGER.error("Application configuration error: %s", error)

    return error_response(
        code="configuration_error",
        message="Application configuration is invalid.",
        status_code=500,
    )
