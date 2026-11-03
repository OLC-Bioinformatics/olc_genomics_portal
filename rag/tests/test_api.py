#!/usr/bin/env python3

"""Tests for the RedmineAssistant Flask API."""

from dataclasses import replace

import pytest

import api
from retrieval import RetrievalError, SearchResult


@pytest.fixture
def client(monkeypatch):
    """Create a Flask test client."""
    monkeypatch.setattr(
        api,
        "record_retrieval_request",
        lambda **kwargs: None,
    )
    api.app.config.update(
        TESTING=True,
    )

    with api.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def trusted_settings(monkeypatch):
    """Configure a deterministic trusted-service token for one test."""
    updated_settings = replace(
        api.settings,
        trusted_service_token="test-service-token",
        trusted_access_header="X-Redmine-Assistant-Access",
    )
    monkeypatch.setattr(
        api,
        "settings",
        updated_settings,
    )
    return updated_settings


def search_result(
    *,
    rank: int = 1,
    source_path: str = "analysis/mobsuite.md",
    source_url: str | None = "analysis/mobsuite.md",
    document_title: str = "MobSuite",
    heading_path: str = "MobSuite > What does it do?",
    content: str = "MobSuite detects plasmids.",
    score: float = 0.91,
    access_level: str = "standard",
    chunk_key: str = "analysis/mobsuite.md::0000",
) -> SearchResult:
    """Create a representative semantic-search result."""
    return SearchResult(
        rank=rank,
        chunk_key=chunk_key,
        source_path=source_path,
        source_url=source_url,
        document_title=document_title,
        heading_path=heading_path,
        content=content,
        score=score,
        access_level=access_level,
    )


def test_health_endpoint_returns_liveness(client) -> None:
    """The liveness endpoint does not require dependencies."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "service": "redmine-assistant-rag",
    }
    assert response.headers["X-Request-ID"]


def test_readiness_endpoint_returns_database_status(
    client,
    monkeypatch,
) -> None:
    """The readiness endpoint reports database state."""
    monkeypatch.setattr(
        api,
        "check_database_connection",
        lambda: None,
    )
    monkeypatch.setattr(
        api,
        "get_database_status",
        lambda: {
            "migration_count": 2,
            "documents": 51,
            "chunks": 857,
        },
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ready",
        "database": "connected",
        "schema_migrations": 2,
        "documents": 51,
        "chunks": 857,
    }


def test_readiness_endpoint_returns_503_when_database_fails(
    client,
    monkeypatch,
) -> None:
    """Database failures make the service not ready."""
    def fail_database_check():
        raise RuntimeError("Database unavailable")

    monkeypatch.setattr(
        api,
        "check_database_connection",
        fail_database_check,
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.get_json() == {
        "status": "not_ready",
        "database": "unavailable",
    }


def test_search_returns_ranked_results(
    client,
    monkeypatch,
) -> None:
    """The legacy endpoint returns complete standard results."""
    received_arguments = {}

    def fake_retrieve_chunks(
        query,
        limit,
        include_internal,
    ):
        received_arguments.update(
            {
                "query": query,
                "limit": limit,
                "include_internal": include_internal,
            }
        )
        return [
            search_result(),
            search_result(
                rank=2,
                source_path="index.md",
                source_url="index.md",
                document_title="OLC Redmine Automator",
                content="Use MobSuite.",
                score=0.84,
                chunk_key="index.md::0013",
            ),
        ]

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/search",
        json={
            "query": "Which automator detects plasmids?",
            "limit": 2,
        },
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["count"] == 2
    assert body["limit"] == 2
    assert body["results"][0]["content"] == (
        "MobSuite detects plasmids."
    )
    assert received_arguments == {
        "query": "Which automator detects plasmids?",
        "limit": 2,
        "include_internal": False,
    }


def test_search_uses_default_limit(
    client,
    monkeypatch,
) -> None:
    """Omitting the limit uses the configured default."""
    received = {}

    def fake_retrieve_chunks(**kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/search",
        json={"query": "plasmids"},
    )

    assert response.status_code == 200
    assert response.get_json()["limit"] == api.settings.top_k
    assert received["limit"] == api.settings.top_k


@pytest.mark.parametrize(
    ("payload", "content_type", "expected_code", "expected_status"),
    [
        ("query=plasmids", "application/x-www-form-urlencoded", "unsupported_media_type", 415),
        ('{"query": ', "application/json", "invalid_json", 400),
    ],
)
def test_search_rejects_invalid_request_encoding(
    client,
    payload,
    content_type,
    expected_code,
    expected_status,
) -> None:
    """The legacy endpoint requires valid JSON."""
    response = client.post(
        "/search",
        data=payload,
        content_type=content_type,
    )

    assert response.status_code == expected_status
    assert response.get_json()["error"]["code"] == expected_code


def test_search_rejects_non_object_json(client) -> None:
    """The JSON body must be an object."""
    response = client.post(
        "/search",
        json=["plasmids"],
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_request"


def test_search_rejects_missing_query(client) -> None:
    """The query field is required."""
    response = client.post(
        "/search",
        json={"limit": 5},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_query"


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        None,
        123,
        True,
        ["plasmids"],
        {"tool": "MobSuite"},
    ],
)
def test_search_rejects_invalid_query(
    client,
    query,
) -> None:
    """Only non-empty string queries are accepted."""
    response = client.post(
        "/search",
        json={"query": query},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "limit",
    [
        0,
        -1,
        11,
        True,
        False,
        "5",
        1.5,
    ],
)
def test_search_rejects_invalid_limit(
    client,
    limit,
) -> None:
    """Search limits must be valid configured integers."""
    response = client.post(
        "/search",
        json={
            "query": "plasmids",
            "limit": limit,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_request"


def test_search_returns_safe_retrieval_error(
    client,
    monkeypatch,
) -> None:
    """Expected retrieval failures receive a safe 503 response."""
    def fail_retrieval(**kwargs):
        raise RetrievalError("Secret database credentials")

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fail_retrieval,
    )

    response = client.post(
        "/search",
        json={"query": "plasmids"},
    )

    body = response.get_json()
    assert response.status_code == 503
    assert body["error"]["code"] == "search_unavailable"
    assert "Secret" not in str(body)


def test_search_returns_safe_unexpected_error(
    client,
    monkeypatch,
) -> None:
    """Unexpected failures receive a safe 500 response."""
    def fail_retrieval(**kwargs):
        raise RuntimeError("Sensitive internal exception")

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fail_retrieval,
    )

    response = client.post(
        "/search",
        json={"query": "plasmids"},
    )

    body = response.get_json()
    assert response.status_code == 500
    assert body["error"]["code"] == "internal_error"
    assert "Sensitive" not in str(body)


def test_legacy_search_remains_standard_only(
    client,
    monkeypatch,
    trusted_settings,
) -> None:
    """Trusted headers cannot enable internal legacy search."""
    received = {}

    def fake_retrieve_chunks(**kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/search",
        headers={
            "Authorization": "Bearer test-service-token",
            "X-Redmine-Assistant-Access": "internal",
        },
        json={
            "query": "Merge",
            "include_internal": True,
        },
    )

    assert response.status_code == 200
    assert received["include_internal"] is False


def test_legacy_search_filters_internal_results(
    client,
    monkeypatch,
) -> None:
    """Internal chunks are omitted by the legacy endpoint."""
    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        lambda **kwargs: [
            search_result(),
            search_result(
                rank=2,
                source_path="internal_only/merge.md",
                source_url="internal_only/merge.md",
                access_level="internal",
            ),
        ],
    )

    response = client.post(
        "/search",
        json={"query": "Merge"},
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["count"] == 1
    assert body["results"][0]["access_level"] == "standard"


def test_v1_returns_retrieval_contract(
    client,
    monkeypatch,
) -> None:
    """A successful request returns the stable v1 schema."""
    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        lambda **kwargs: [search_result()],
    )

    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "  plasmids  ",
            "limit": 1,
        },
    )

    body = response.get_json()
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert body["query"] == "plasmids"
    assert body["answer_mode"] == "retrieval_only"
    assert body["answer"] is None
    assert body["abstained"] is False
    assert body["access_context"] == "standard"
    assert body["result_count"] == 1
    assert body["limit"] == 1
    assert body["sources"][0]["excerpt"] == (
        "MobSuite detects plasmids."
    )
    assert "content" not in body["sources"][0]


def test_v1_anonymous_request_remains_standard(
    client,
    monkeypatch,
) -> None:
    """Anonymous v1 requests receive standard access."""
    received = {}

    def fake_retrieve_chunks(**kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/api/v1/retrieve",
        json={"query": "ConFindr"},
    )

    assert response.status_code == 200
    assert received["include_internal"] is False
    assert response.get_json()["access_context"] == "standard"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("include_internal", True),
        ("access_context", "internal"),
    ],
)
def test_v1_rejects_json_access_escalation(
    client,
    field,
    value,
) -> None:
    """JSON request fields cannot select internal access."""
    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "Merge",
            field: value,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == (
        "forbidden_access_context"
    )


def test_v1_trusted_standard_request_remains_standard(
    client,
    monkeypatch,
    trusted_settings,
) -> None:
    """A trusted standard assertion remains standard-only."""
    received = {}

    def fake_retrieve_chunks(**kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/api/v1/retrieve",
        headers={
            "Authorization": "Bearer test-service-token",
            "X-Redmine-Assistant-Access": "standard",
        },
        json={"query": "ConFindr"},
    )

    assert response.status_code == 200
    assert received["include_internal"] is False
    assert response.get_json()["access_context"] == "standard"


def test_v1_trusted_internal_request_enables_internal(
    client,
    monkeypatch,
    trusted_settings,
) -> None:
    """A trusted internal assertion enables internal retrieval."""
    received = {}

    def fake_retrieve_chunks(**kwargs):
        received.update(kwargs)
        return [
            search_result(
                source_path="internal_only/merge.md",
                source_url="internal_only/merge.md",
                document_title="Merge",
                access_level="internal",
            )
        ]

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/api/v1/retrieve",
        headers={
            "Authorization": "Bearer test-service-token",
            "X-Redmine-Assistant-Access": "internal",
        },
        json={"query": "Merge"},
    )

    body = response.get_json()
    assert response.status_code == 200
    assert received["include_internal"] is True
    assert body["access_context"] == "internal"
    assert body["result_count"] == 1
    assert body["sources"][0]["access_level"] == "internal"


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer wrong-token",
        "Basic wrong-scheme",
        "Bearer",
    ],
)
def test_v1_rejects_untrusted_internal_context(
    client,
    trusted_settings,
    authorization,
) -> None:
    """Internal access requires the trusted service token."""
    headers = {
        "X-Redmine-Assistant-Access": "internal",
    }

    if authorization is not None:
        headers["Authorization"] = authorization

    response = client.post(
        "/api/v1/retrieve",
        headers=headers,
        json={"query": "Merge"},
    )

    body = response.get_json()
    assert response.status_code == 403
    assert body["error"]["code"] == "forbidden_access_context"
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_v1_rejects_invalid_trusted_access_value(
    client,
    trusted_settings,
) -> None:
    """Only standard and internal trusted contexts are accepted."""
    response = client.post(
        "/api/v1/retrieve",
        headers={
            "Authorization": "Bearer test-service-token",
            "X-Redmine-Assistant-Access": "administrator",
        },
        json={"query": "Merge"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == (
        "forbidden_access_context"
    )


def test_v1_standard_response_filters_internal_results(
    client,
    monkeypatch,
) -> None:
    """Internal chunks are removed from standard v1 responses."""
    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        lambda **kwargs: [
            search_result(),
            search_result(
                rank=2,
                source_path="internal_only/merge.md",
                source_url="internal_only/merge.md",
                access_level="internal",
            ),
        ],
    )

    response = client.post(
        "/api/v1/retrieve",
        json={"query": "Merge"},
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["result_count"] == 1
    assert body["sources"][0]["access_level"] == "standard"


def test_v1_bounds_excerpts(
    client,
    monkeypatch,
) -> None:
    """The v1 endpoint does not return unbounded chunk text."""
    content = "word " * (
        api.settings.max_excerpt_chars + 10
    )

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        lambda **kwargs: [
            search_result(content=content)
        ],
    )

    response = client.post(
        "/api/v1/retrieve",
        json={"query": "plasmids"},
    )

    excerpt = response.get_json()["sources"][0]["excerpt"]
    assert excerpt.endswith("…")
    assert len(excerpt) <= api.settings.max_excerpt_chars + 1


def test_v1_rejects_oversized_query(client) -> None:
    """Queries above the configured maximum receive a 400 response."""
    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "x" * (
                api.settings.max_query_chars + 1
            ),
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_request"


def test_v1_returns_safe_retrieval_error(
    client,
    monkeypatch,
) -> None:
    """Retrieval failures do not expose exception details."""
    def fail_retrieval(**kwargs):
        raise RetrievalError("Secret database details")

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fail_retrieval,
    )

    response = client.post(
        "/api/v1/retrieve",
        json={"query": "plasmids"},
    )

    body = response.get_json()
    assert response.status_code == 503
    assert body["error"]["code"] == "search_unavailable"
    assert "Secret" not in str(body)
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_v1_persists_retrieval_event(
    client,
    monkeypatch,
) -> None:
    """Successful retrieval stores request metadata for later feedback."""
    captured = {}
    monkeypatch.setattr(api, "retrieve_chunks", lambda **kwargs: [search_result()])
    monkeypatch.setattr(
        api,
        "record_retrieval_request",
        lambda **kwargs: captured.update(kwargs),
    )

    response = client.post("/api/v1/retrieve", json={"query": "plasmids"})

    assert response.status_code == 200
    body = response.get_json()
    assert captured == {
        "request_id": body["request_id"],
        "query": "plasmids",
        "access_context": "standard",
        "result_chunk_keys": ["analysis/mobsuite.md::0000"],
    }


def test_feedback_requires_trusted_authentication(client) -> None:
    """Feedback cannot be written by an anonymous caller."""
    response = client.post(
        "/api/v1/feedback",
        json={
            "request_id": "11111111-1111-4111-8111-111111111111",
            "rating": "helpful",
        },
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authentication_required"


def test_feedback_records_helpful_rating(
    client,
    monkeypatch,
    trusted_settings,
) -> None:
    """Trusted Redmine can record helpful retrieval feedback."""
    captured = {}
    monkeypatch.setattr(
        api,
        "save_retrieval_feedback",
        lambda **kwargs: captured.update(kwargs) or True,
    )
    retrieval_request_id = "11111111-1111-4111-8111-111111111111"

    response = client.post(
        "/api/v1/feedback",
        headers={
            "Authorization": "Bearer test-service-token",
            "X-Redmine-Assistant-Access": "standard",
        },
        json={
            "request_id": retrieval_request_id,
            "rating": "helpful",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "request_id": retrieval_request_id,
        "access_context": "standard",
        "rating": "helpful",
        "reason": None,
        "comment": None,
    }
    assert response.get_json()["retrieval_request_id"] == retrieval_request_id


def test_feedback_records_unhelpful_reason_and_comment(
    client,
    monkeypatch,
    trusted_settings,
) -> None:
    """Unhelpful feedback retains a controlled reason and bounded comment."""
    captured = {}
    monkeypatch.setattr(
        api,
        "save_retrieval_feedback",
        lambda **kwargs: captured.update(kwargs) or True,
    )
    response = client.post(
        "/api/v1/feedback",
        headers={
            "Authorization": "Bearer test-service-token",
            "X-Redmine-Assistant-Access": "internal",
        },
        json={
            "request_id": "22222222-2222-4222-8222-222222222222",
            "rating": "unhelpful",
            "reason": "missing_documentation",
            "comment": "Expected runtime was not documented.",
        },
    )

    assert response.status_code == 200
    assert captured["access_context"] == "internal"
    assert captured["reason"] == "missing_documentation"
    assert captured["comment"] == "Expected runtime was not documented."


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"request_id": "not-a-uuid", "rating": "helpful"}, "invalid_request_id"),
        (
            {
                "request_id": "11111111-1111-4111-8111-111111111111",
                "rating": "unhelpful",
            },
            "invalid_reason",
        ),
        (
            {
                "request_id": "11111111-1111-4111-8111-111111111111",
                "rating": "helpful",
                "access_context": "internal",
            },
            "forbidden_access_context",
        ),
    ],
)
def test_feedback_rejects_invalid_payloads(
    client,
    trusted_settings,
    payload,
    expected_code,
) -> None:
    """Feedback validation rejects malformed and access-selecting input."""
    response = client.post(
        "/api/v1/feedback",
        headers={"Authorization": "Bearer test-service-token"},
        json=payload,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == expected_code


def test_feedback_hides_access_mismatch_as_not_found(
    client,
    monkeypatch,
    trusted_settings,
) -> None:
    """A request from another access context is not accepted."""
    monkeypatch.setattr(api, "save_retrieval_feedback", lambda **kwargs: False)
    response = client.post(
        "/api/v1/feedback",
        headers={"Authorization": "Bearer test-service-token"},
        json={
            "request_id": "11111111-1111-4111-8111-111111111111",
            "rating": "helpful",
        },
    )
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "retrieval_not_found"
