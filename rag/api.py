#!/usr/bin/env python3

"""Flask API for the RedmineAssistant retrieval service."""

# Standard library imports
import hmac
import logging
import time
from typing import Any
from uuid import UUID, uuid4

# Third-party imports
from flask import Flask, g, jsonify, request

# Local imports
from config import ConfigurationError, settings
from database import (
    check_database_connection,
    get_database_status,
    record_retrieval_request,
    save_retrieval_feedback,
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

VALID_ACCESS_CONTEXTS = frozenset(
    {
        "standard",
        "internal",
    }
)

VALID_FEEDBACK_RATINGS = frozenset({"helpful", "unhelpful"})
VALID_UNHELPFUL_REASONS = frozenset(
    {
        "irrelevant_results",
        "missing_documentation",
        "unclear_documentation",
        "outdated_documentation",
        "insufficient_detail",
        "other",
    }
)
MAX_FEEDBACK_COMMENT_CHARS = 1_000


class APIRequestError(RuntimeError):
    """Raised when an HTTP retrieval request is invalid."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
    ) -> None:
        """Initialize a safe API validation error."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AccessContextError(RuntimeError):
    """Raised when a caller attempts unauthorized access elevation."""


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
    """Create a consistent JSON error response."""
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


def parse_retrieval_request(
    *,
    reject_access_fields: bool,
) -> tuple[str, int]:
    """Validate and normalize a JSON retrieval request."""
    if not request.is_json:
        raise APIRequestError(
            code="unsupported_media_type",
            message=(
                "The request Content-Type must be application/json."
            ),
            status_code=415,
        )

    request_body = request.get_json(
        silent=True,
    )

    if request_body is None:
        raise APIRequestError(
            code="invalid_json",
            message="The request body must contain valid JSON.",
            status_code=400,
        )

    if not isinstance(request_body, dict):
        raise APIRequestError(
            code="invalid_request",
            message="The JSON request body must be an object.",
            status_code=400,
        )

    if reject_access_fields:
        prohibited_fields = sorted(
            PROHIBITED_ACCESS_FIELDS.intersection(
                request_body
            )
        )

        if prohibited_fields:
            raise APIRequestError(
                code="forbidden_access_context",
                message=(
                    "Access context is assigned by a trusted "
                    "server integration."
                ),
                status_code=400,
            )

    if "query" not in request_body:
        raise APIRequestError(
            code="missing_query",
            message="The request body must include a query.",
            status_code=400,
        )

    try:
        query = validate_query(
            request_body["query"]
        )
        limit = validate_limit(
            request_body.get("limit")
        )
    except RetrievalError as exc:
        raise APIRequestError(
            code="invalid_request",
            message=str(exc),
            status_code=400,
        ) from exc

    return query, limit


def parse_feedback_request() -> tuple[str, str, str | None, str | None]:
    """Validate and normalize a JSON retrieval-feedback request."""
    if not request.is_json:
        raise APIRequestError(
            code="unsupported_media_type",
            message="The request Content-Type must be application/json.",
            status_code=415,
        )

    body = request.get_json(silent=True)
    if body is None:
        raise APIRequestError(
            code="invalid_json",
            message="The request body must contain valid JSON.",
            status_code=400,
        )
    if not isinstance(body, dict):
        raise APIRequestError(
            code="invalid_request",
            message="The JSON request body must be an object.",
            status_code=400,
        )

    forbidden = sorted(PROHIBITED_ACCESS_FIELDS.intersection(body))
    if forbidden:
        raise APIRequestError(
            code="forbidden_access_context",
            message="Access context is assigned by a trusted server integration.",
            status_code=400,
        )

    allowed_fields = {"request_id", "rating", "reason", "comment"}
    unknown_fields = sorted(set(body) - allowed_fields)
    if unknown_fields:
        raise APIRequestError(
            code="invalid_request",
            message="The feedback request contains unsupported fields.",
            status_code=400,
        )

    request_id = body.get("request_id")
    if not isinstance(request_id, str):
        raise APIRequestError(
            code="invalid_request_id",
            message="A valid retrieval request identifier is required.",
            status_code=400,
        )
    try:
        normalized_request_id = str(UUID(request_id))
    except (ValueError, AttributeError) as exc:
        raise APIRequestError(
            code="invalid_request_id",
            message="A valid retrieval request identifier is required.",
            status_code=400,
        ) from exc

    rating = body.get("rating")
    if rating not in VALID_FEEDBACK_RATINGS:
        raise APIRequestError(
            code="invalid_rating",
            message="Feedback rating must be helpful or unhelpful.",
            status_code=400,
        )

    reason = body.get("reason")
    if rating == "helpful":
        if reason not in (None, ""):
            raise APIRequestError(
                code="invalid_reason",
                message="Helpful feedback must not include an unhelpful reason.",
                status_code=400,
            )
        reason = None
    elif reason not in VALID_UNHELPFUL_REASONS:
        raise APIRequestError(
            code="invalid_reason",
            message="Unhelpful feedback must include a valid reason.",
            status_code=400,
        )

    comment = body.get("comment")
    if comment is None:
        normalized_comment = None
    elif not isinstance(comment, str):
        raise APIRequestError(
            code="invalid_comment",
            message="Feedback comment must be text.",
            status_code=400,
        )
    else:
        normalized_comment = comment.strip() or None
        if normalized_comment and len(normalized_comment) > MAX_FEEDBACK_COMMENT_CHARS:
            raise APIRequestError(
                code="invalid_comment",
                message=(
                    "Feedback comment cannot exceed "
                    f"{MAX_FEEDBACK_COMMENT_CHARS} characters."
                ),
                status_code=400,
            )

    return normalized_request_id, rating, reason, normalized_comment


def serialize_search_result(
    result: SearchResult,
) -> dict[str, Any]:
    """Convert a result for the legacy search response."""
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
    """Return a trimmed excerpt within the configured size limit."""
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
    """Convert a result into a bounded v1 source object."""
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


def bearer_token() -> str | None:
    """Extract a Bearer token from the Authorization header."""
    authorization = request.headers.get(
        "Authorization",
        "",
    ).strip()

    scheme, separator, credentials = authorization.partition(" ")

    if (
        not separator
        or scheme.lower() != "bearer"
        or not credentials.strip()
    ):
        return None

    return credentials.strip()


def trusted_service_authenticated() -> bool:
    """Return whether the caller supplied the trusted service token."""
    configured_token = settings.trusted_service_token

    if not configured_token:
        return False

    supplied_token = bearer_token()

    if supplied_token is None:
        return False

    return hmac.compare_digest(
        supplied_token,
        configured_token,
    )


def determine_access_context() -> str:
    """Derive documentation access from trusted server headers."""
    requested_context = request.headers.get(
        settings.trusted_access_header,
        "",
    ).strip().lower()

    if not requested_context:
        return "standard"

    if requested_context not in VALID_ACCESS_CONTEXTS:
        raise AccessContextError(
            "The trusted access context is invalid."
        )

    if not trusted_service_authenticated():
        raise AccessContextError(
            "The requested access context is not authorized."
        )

    return requested_context


def results_for_access(
    results: list[SearchResult],
    access_context: str,
) -> list[SearchResult]:
    """Defensively enforce the resolved access context."""
    if access_context == "internal":
        return results

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
            "documentation to a standard request",
            current_request_id(),
        )

    return filtered


def run_retrieval(
    *,
    query: str,
    limit: int,
    access_context: str,
) -> list[SearchResult]:
    """Run retrieval and enforce the resolved access context."""
    results = retrieve_chunks(
        query=query,
        limit=limit,
        include_internal=(
            access_context == "internal"
        ),
    )

    return results_for_access(
        results,
        access_context,
    )


@app.get("/health")
def health():
    """Return process liveness without checking dependencies."""
    return jsonify(
        {
            "status": "ok",
            "service": "redmine-assistant-rag",
        }
    )


@app.get("/health/ready")
def readiness():
    """Return database-backed application readiness."""
    try:
        check_database_connection()
        database_status = get_database_status()
    except Exception:
        LOGGER.exception(
            "RAG service readiness check failed"
        )

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
            "schema_migrations": database_status[
                "migration_count"
            ],
            "documents": database_status["documents"],
            "chunks": database_status["chunks"],
        }
    )


@app.post("/search")
def search():
    """Search standard documentation through the legacy endpoint."""
    try:
        query, limit = parse_retrieval_request(
            reject_access_fields=False,
        )
    except APIRequestError as exc:
        return error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
        )

    started = time.monotonic()

    try:
        results = run_retrieval(
            query=query,
            limit=limit,
            access_context="standard",
        )
    except RetrievalError:
        LOGGER.exception(
            "request_id=%s legacy search unavailable",
            current_request_id(),
        )

        return error_response(
            code="search_unavailable",
            message=(
                "The documentation search service is "
                "temporarily unavailable."
            ),
            status_code=503,
        )
    except Exception:
        LOGGER.exception(
            "request_id=%s unexpected legacy-search failure",
            current_request_id(),
        )

        return error_response(
            code="internal_error",
            message=(
                "An unexpected error occurred while "
                "searching the documentation."
            ),
            status_code=500,
        )

    LOGGER.info(
        "request_id=%s endpoint=legacy-search "
        "access_context=standard query_length=%s limit=%s "
        "results=%s elapsed_ms=%.1f",
        current_request_id(),
        len(query),
        limit,
        len(results),
        (time.monotonic() - started) * 1000.0,
    )

    return jsonify(
        {
            "status": "ok",
            "query": query,
            "count": len(results),
            "limit": limit,
            "results": [
                serialize_search_result(result)
                for result in results
            ],
        }
    )


@app.post("/api/v1/retrieve")
def retrieve_v1():
    """Return access-aware, structured retrieval evidence."""
    try:
        query, limit = parse_retrieval_request(
            reject_access_fields=True,
        )
    except APIRequestError as exc:
        return error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
        )

    try:
        access_context = determine_access_context()
    except AccessContextError:
        LOGGER.warning(
            "request_id=%s unauthorized access-context request",
            current_request_id(),
        )

        return error_response(
            code="forbidden_access_context",
            message=(
                "The requested documentation access context "
                "is not authorized."
            ),
            status_code=403,
        )

    started = time.monotonic()

    try:
        results = run_retrieval(
            query=query,
            limit=limit,
            access_context=access_context,
        )
    except RetrievalError:
        LOGGER.exception(
            "request_id=%s semantic retrieval unavailable",
            current_request_id(),
        )

        return error_response(
            code="search_unavailable",
            message=(
                "The documentation search service is "
                "temporarily unavailable."
            ),
            status_code=503,
        )
    except Exception:
        LOGGER.exception(
            "request_id=%s unexpected retrieval failure",
            current_request_id(),
        )

        return error_response(
            code="internal_error",
            message=(
                "An unexpected error occurred while "
                "searching the documentation."
            ),
            status_code=500,
        )

    LOGGER.info(
        "request_id=%s endpoint=v1-retrieve "
        "access_context=%s query_length=%s limit=%s "
        "results=%s elapsed_ms=%.1f",
        current_request_id(),
        access_context,
        len(query),
        limit,
        len(results),
        (time.monotonic() - started) * 1000.0,
    )

    sources = [
        serialize_retrieval_source(result)
        for result in results
    ]

    try:
        record_retrieval_request(
            request_id=current_request_id(),
            query=query,
            access_context=access_context,
            result_chunk_keys=[result.chunk_key for result in results],
        )
    except Exception:
        LOGGER.exception(
            "request_id=%s failed to persist retrieval telemetry",
            current_request_id(),
        )
        return error_response(
            code="telemetry_unavailable",
            message=(
                "The documentation search completed, but the response "
                "could not be recorded. Please try again."
            ),
            status_code=503,
        )

    return jsonify(
        {
            "status": "ok",
            "request_id": current_request_id(),
            "query": query,
            "answer_mode": "retrieval_only",
            "answer": None,
            "abstained": False,
            "access_context": access_context,
            "result_count": len(sources),
            "limit": limit,
            "sources": sources,
        }
    )


@app.post("/api/v1/feedback")
def feedback_v1():
    """Store feedback for a retrieval response from trusted Redmine."""
    if not trusted_service_authenticated():
        return error_response(
            code="authentication_required",
            message="Trusted service authentication is required.",
            status_code=401,
        )

    try:
        access_context = determine_access_context()
    except AccessContextError:
        LOGGER.warning(
            "request_id=%s unauthorized feedback access-context request",
            current_request_id(),
        )
        return error_response(
            code="forbidden_access_context",
            message="The requested documentation access context is not authorized.",
            status_code=403,
        )

    try:
        retrieval_request_id, rating, reason, comment = parse_feedback_request()
    except APIRequestError as exc:
        return error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
        )

    try:
        saved = save_retrieval_feedback(
            request_id=retrieval_request_id,
            access_context=access_context,
            rating=rating,
            reason=reason,
            comment=comment,
        )
    except Exception:
        LOGGER.exception(
            "request_id=%s feedback storage failed",
            current_request_id(),
        )
        return error_response(
            code="feedback_unavailable",
            message="Feedback could not be recorded. Please try again.",
            status_code=503,
        )

    if not saved:
        return error_response(
            code="retrieval_not_found",
            message="The retrieval response could not be found.",
            status_code=404,
        )

    LOGGER.info(
        "request_id=%s endpoint=v1-feedback retrieval_request_id=%s rating=%s",
        current_request_id(),
        retrieval_request_id,
        rating,
    )
    return jsonify(
        {
            "status": "ok",
            "request_id": current_request_id(),
            "retrieval_request_id": retrieval_request_id,
            "feedback": {
                "target_type": "retrieval_response",
                "rating": rating,
                "reason": reason,
            },
        }
    )


@app.errorhandler(ConfigurationError)
def configuration_error(error):
    """Return a generic response for configuration failures."""
    LOGGER.error(
        "Application configuration error: %s",
        error,
    )

    return error_response(
        code="configuration_error",
        message="Application configuration is invalid.",
        status_code=500,
    )
