#!/usr/bin/env python3

"""Tests for the RedmineAssistant Flask API."""

import pytest

import api
from retrieval import RetrievalError, SearchResult


@pytest.fixture
def client():
    """Create a Flask test client."""
    api.app.config.update(
        TESTING=True,
    )

    with api.app.test_client() as test_client:
        yield test_client


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
            "chunks": 404,
        },
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ready",
        "database": "connected",
        "schema_migrations": 2,
        "documents": 51,
        "chunks": 404,
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
    """A valid query returns serialized semantic-search results."""
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
                heading_path=(
                    "OLC Redmine Automator > Possible Analyses "
                    "> Detect and type plasmids"
                ),
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

    assert response.status_code == 200

    response_body = response.get_json()

    assert response_body["status"] == "ok"
    assert response_body["query"] == (
        "Which automator detects plasmids?"
    )
    assert response_body["limit"] == 2
    assert response_body["count"] == 2

    assert response_body["results"][0] == {
        "rank": 1,
        "score": 0.91,
        "chunk_key": "analysis/mobsuite.md::0000",
        "source_path": "analysis/mobsuite.md",
        "source_url": "analysis/mobsuite.md",
        "document_title": "MobSuite",
        "heading_path": "MobSuite > What does it do?",
        "content": "MobSuite detects plasmids.",
        "access_level": "standard",
    }

    assert received_arguments == {
        "query": "Which automator detects plasmids?",
        "limit": 2,
        "include_internal": False,
    }


def test_search_uses_default_limit(
    client,
    monkeypatch,
) -> None:
    """Omitting limit uses the configured retrieval default."""
    received_limit = None

    def fake_retrieve_chunks(
        query,
        limit,
        include_internal,
    ):
        nonlocal received_limit
        received_limit = limit

        return []

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/search",
        json={
            "query": "Which automator detects plasmids?",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["limit"] == 5
    assert received_limit == 5


def test_search_rejects_non_json_request(client) -> None:
    """The search endpoint only accepts JSON requests."""
    response = client.post(
        "/search",
        data="query=plasmids",
        content_type="application/x-www-form-urlencoded",
    )

    assert response.status_code == 415

    response_body = response.get_json()

    assert response_body["error"]["code"] == (
        "unsupported_media_type"
    )


def test_search_rejects_malformed_json(client) -> None:
    """Malformed JSON receives a safe validation error."""
    response = client.post(
        "/search",
        data='{"query": ',
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_json"


def test_search_rejects_non_object_json(client) -> None:
    """The JSON body must be an object."""
    response = client.post(
        "/search",
        json=[
            "Which automator detects plasmids?"
        ],
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == (
        "invalid_request"
    )


def test_search_rejects_missing_query(client) -> None:
    """The query field is required."""
    response = client.post(
        "/search",
        json={
            "limit": 5,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == (
        "missing_query"
    )


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
        json={
            "query": query,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == (
        "invalid_request"
    )


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
            "query": "Which automator detects plasmids?",
            "limit": limit,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == (
        "invalid_request"
    )


def test_search_returns_503_for_retrieval_error(
    client,
    monkeypatch,
) -> None:
    """Expected retrieval failures receive a safe 503 response."""
    def fail_retrieval(
        query,
        limit,
        include_internal,
    ):
        raise RetrievalError(
            "Database credentials and internal details"
        )

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fail_retrieval,
    )

    response = client.post(
        "/search",
        json={
            "query": "Which automator detects plasmids?",
        },
    )

    assert response.status_code == 503

    response_body = response.get_json()

    assert response_body["error"]["code"] == (
        "search_unavailable"
    )
    assert "credentials" not in str(response_body)
    assert "internal details" not in str(response_body)


def test_search_returns_500_for_unexpected_error(
    client,
    monkeypatch,
) -> None:
    """Unexpected failures do not expose exception details."""
    def fail_retrieval(
        query,
        limit,
        include_internal,
    ):
        raise RuntimeError(
            "Sensitive unexpected internal exception"
        )

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fail_retrieval,
    )

    response = client.post(
        "/search",
        json={
            "query": "Which automator detects plasmids?",
        },
    )

    assert response.status_code == 500

    response_body = response.get_json()

    assert response_body["error"]["code"] == "internal_error"
    assert "Sensitive" not in str(response_body)


def test_search_always_disables_internal_retrieval(
    client,
    monkeypatch,
) -> None:
    """Request fields cannot enable internal-document retrieval."""
    received_include_internal = None

    def fake_retrieve_chunks(
        query,
        limit,
        include_internal,
    ):
        nonlocal received_include_internal
        received_include_internal = include_internal

        return []

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/search",
        json={
            "query": "How do I use Merge?",
            "include_internal": True,
        },
    )

    assert response.status_code == 200
    assert received_include_internal is False


def test_search_defensively_removes_internal_results(
    client,
    monkeypatch,
) -> None:
    """Internal results are omitted even if retrieval returns them."""
    def fake_retrieve_chunks(
        query,
        limit,
        include_internal,
    ):
        return [
            search_result(),
            search_result(
                rank=2,
                source_path="internal_only/merge.md",
                source_url="internal_only/merge.md",
                document_title="Merge",
                heading_path="Merge > Description",
                content="Internal merge instructions.",
                score=0.85,
                access_level="internal",
                chunk_key="internal_only/merge.md::0001",
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
            "query": "How do I use Merge?",
        },
    )

    assert response.status_code == 200

    response_body = response.get_json()

    assert response_body["count"] == 1
    assert len(response_body["results"]) == 1
    assert response_body["results"][0]["source_path"] == (
        "analysis/mobsuite.md"
    )

    assert all(
        not result["source_path"].startswith("internal_only/")
        for result in response_body["results"]
    )
