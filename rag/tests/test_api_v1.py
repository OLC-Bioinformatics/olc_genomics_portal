#!/usr/bin/env python3

"""Tests for the versioned retrieval API."""

import pytest

import api
from retrieval import (
    RetrievalError,
    SearchResult,
)


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
    """Create a representative retrieval result."""
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


def test_v1_returns_retrieval_contract(
    client,
    monkeypatch,
) -> None:
    """A successful request returns the stable v1 schema."""
    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        lambda **kwargs: [
            search_result()
        ],
    )

    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "  plasmids  ",
            "limit": 1,
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert response.headers["X-Request-ID"] == (
        body["request_id"]
    )
    assert body["status"] == "ok"
    assert body["query"] == "plasmids"
    assert body["answer_mode"] == "retrieval_only"
    assert body["answer"] is None
    assert body["abstained"] is False
    assert body["access_context"] == "standard"
    assert body["result_count"] == 1
    assert body["limit"] == 1

    source = body["sources"][0]

    assert source["source_path"] == (
        "analysis/mobsuite.md"
    )
    assert source["excerpt"] == (
        "MobSuite detects plasmids."
    )
    assert "content" not in source


def test_v1_always_uses_standard_retrieval(
    client,
    monkeypatch,
) -> None:
    """The public v1 endpoint cannot enable internal retrieval."""
    received = {}

    def fake_retrieve_chunks(**kwargs):
        received.update(
            kwargs
        )
        return []

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fake_retrieve_chunks,
    )

    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "How do I use Merge?",
        },
    )

    assert response.status_code == 200
    assert received["include_internal"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("include_internal", True),
        ("access_context", "internal"),
    ],
)
def test_v1_rejects_client_access_escalation(
    client,
    field,
    value,
) -> None:
    """Clients cannot select an internal access context."""
    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "How do I use Merge?",
            field: value,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == (
        "forbidden_access_context"
    )


def test_v1_defensively_filters_internal_results(
    client,
    monkeypatch,
) -> None:
    """Internal chunks are removed from standard responses."""
    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        lambda **kwargs: [
            search_result(),
            search_result(
                rank=2,
                source_path="internal_only/merge.md",
                source_url="internal_only/merge.md",
                document_title="Merge",
                access_level="internal",
            ),
        ],
    )

    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "Merge",
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["result_count"] == 1
    assert len(body["sources"]) == 1
    assert body["sources"][0]["access_level"] == (
        "standard"
    )


def test_v1_bounds_excerpts(
    client,
    monkeypatch,
) -> None:
    """The v1 endpoint does not return unbounded chunk text."""
    content = (
        "word "
        * (
            api.settings.max_excerpt_chars + 10
        )
    )

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        lambda **kwargs: [
            search_result(
                content=content,
            )
        ],
    )

    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "plasmids",
        },
    )

    excerpt = (
        response.get_json()
        ["sources"][0]
        ["excerpt"]
    )

    assert excerpt.endswith("…")
    assert len(excerpt) <= (
        api.settings.max_excerpt_chars + 1
    )


def test_v1_rejects_oversized_query(
    client,
) -> None:
    """Queries above the configured limit receive a 400 response."""
    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": (
                "x"
                * (
                    api.settings.max_query_chars + 1
                )
            ),
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == (
        "invalid_request"
    )


def test_v1_returns_safe_retrieval_error(
    client,
    monkeypatch,
) -> None:
    """Retrieval failures do not expose exception details."""
    def fail_retrieval(**kwargs):
        raise RetrievalError(
            "Secret database details"
        )

    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        fail_retrieval,
    )

    response = client.post(
        "/api/v1/retrieve",
        json={
            "query": "plasmids",
        },
    )

    assert response.status_code == 503

    body = response.get_json()

    assert body["error"]["code"] == (
        "search_unavailable"
    )
    assert "Secret" not in str(body)
    assert response.headers["X-Request-ID"] == (
        body["request_id"]
    )
