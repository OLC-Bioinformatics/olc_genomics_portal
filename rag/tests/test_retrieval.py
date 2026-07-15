#!/usr/bin/env python3

"""Tests for semantic documentation retrieval."""

from contextlib import contextmanager

import pytest

import retrieval
from retrieval import (
    RetrievalError,
    retrieve_chunks,
    row_to_search_result,
    validate_limit,
    validate_query,
)


class FakeEmbeddingProvider:
    """Deterministic embedding provider for retrieval tests."""

    def __init__(
        self,
        dimension: int = 384,
    ) -> None:
        """Initialize the fake provider."""
        self.dimension = dimension
        self.queries: list[str] = []

    def embed_query(
        self,
        query: str,
    ) -> list[float]:
        """Return a deterministic query vector."""
        self.queries.append(query)

        vector = [0.0] * self.dimension

        if vector:
            vector[0] = 1.0

        return vector


class FakeCursor:
    """Minimal database cursor used by retrieval tests."""

    def __init__(
        self,
        rows: list[dict],
    ) -> None:
        """Initialize the cursor with rows to return."""
        self.rows = rows
        self.query = None
        self.parameters = None

    def __enter__(self):
        """Enter the cursor context."""
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        """Exit the cursor context."""

    def execute(
        self,
        query,
        parameters,
    ) -> None:
        """Record the executed query and parameters."""
        self.query = query
        self.parameters = parameters

    def fetchall(self) -> list:
        """Return the configured result rows."""
        return self.rows


class FakeConnection:
    """Minimal database connection used by retrieval tests."""

    def __init__(
        self,
        rows: list[dict],
    ) -> None:
        """Initialize the fake connection."""
        self.cursor_instance = FakeCursor(rows)

    def cursor(self) -> FakeCursor:
        """Return the fake cursor."""
        return self.cursor_instance


def result_row(
    *,
    chunk_key: str = "analysis/mobsuite.md::0000",
    source_path: str = "analysis/mobsuite.md",
    source_url: str | None = "analysis/mobsuite.md",
    document_title: str = "MobSuite",
    heading_path: str = "MobSuite > What does it do?",
    content: str = "MobSuite detects plasmids.",
    score: float = 0.91,
    access_level: str = "standard",
) -> dict:
    """Create a representative database result row."""
    return {
        "chunk_key": chunk_key,
        "source_path": source_path,
        "source_url": source_url,
        "document_title": document_title,
        "heading_path": heading_path,
        "content": content,
        "score": score,
        "access_level": access_level,
    }


def install_fake_database(
    monkeypatch,
    rows: list[dict],
) -> FakeConnection:
    """Replace the real database context with a fake connection."""
    connection = FakeConnection(rows)

    @contextmanager
    def fake_database_connection():
        yield connection

    monkeypatch.setattr(
        retrieval,
        "database_connection",
        fake_database_connection,
    )
    monkeypatch.setattr(
        retrieval,
        "register_vector",
        lambda supplied_connection: None,
    )

    return connection


def test_validate_query_trims_whitespace() -> None:
    """Search queries are stripped before embedding."""
    assert validate_query(
        "  Which tool detects plasmids?  "
    ) == "Which tool detects plasmids?"


def test_validate_query_rejects_blank_input() -> None:
    """Blank search queries are rejected."""
    with pytest.raises(
        RetrievalError,
        match="cannot be blank",
    ):
        validate_query("   ")


def test_validate_query_rejects_non_string_input() -> None:
    """Search queries must be strings."""
    with pytest.raises(
        RetrievalError,
        match="must be a string",
    ):
        validate_query(None)


def test_validate_limit_uses_configured_default() -> None:
    """An omitted limit uses the configured top-k."""
    assert validate_limit(None) == 5


def test_validate_limit_rejects_zero() -> None:
    """At least one result must be requested."""
    with pytest.raises(
        RetrievalError,
        match="at least 1",
    ):
        validate_limit(0)


def test_validate_limit_rejects_excessive_value() -> None:
    """The configured maximum result count is enforced."""
    with pytest.raises(
        RetrievalError,
        match="cannot exceed",
    ):
        validate_limit(11)


def test_validate_limit_rejects_boolean() -> None:
    """Boolean values are not accepted as result limits."""
    with pytest.raises(
        RetrievalError,
        match="must be an integer",
    ):
        validate_limit(True)


def test_row_to_search_result_maps_fields() -> None:
    """Database rows are converted to structured results."""
    result = row_to_search_result(
        row=result_row(),
        rank=1,
    )

    assert result.rank == 1
    assert result.source_path == "analysis/mobsuite.md"
    assert result.document_title == "MobSuite"
    assert result.score == pytest.approx(0.91)
    assert result.score_percentage == pytest.approx(91.0)
    assert result.access_level == "standard"


def test_retrieve_chunks_returns_ranked_results(
    monkeypatch,
) -> None:
    """Retrieved rows receive sequential ranks."""
    rows = [
        result_row(
            chunk_key="first",
            score=0.91,
        ),
        result_row(
            chunk_key="second",
            source_path="index.md",
            document_title="OLC Redmine Automator",
            score=0.84,
        ),
    ]

    install_fake_database(
        monkeypatch,
        rows,
    )

    provider = FakeEmbeddingProvider()

    results = retrieve_chunks(
        query="Which tool detects plasmids?",
        limit=2,
        embedding_provider=provider,
    )

    assert len(results) == 2
    assert results[0].rank == 1
    assert results[0].chunk_key == "first"
    assert results[1].rank == 2
    assert results[1].chunk_key == "second"

    assert provider.queries == [
        "Which tool detects plasmids?"
    ]


def test_retrieve_chunks_excludes_internal_by_default(
    monkeypatch,
) -> None:
    """The SQL receives false for internal-content inclusion."""
    connection = install_fake_database(
        monkeypatch,
        [result_row()],
    )

    retrieve_chunks(
        query="Which tool detects plasmids?",
        limit=3,
        embedding_provider=FakeEmbeddingProvider(),
    )

    parameters = connection.cursor_instance.parameters

    assert parameters[1] is False
    assert parameters[2] == 3


def test_retrieve_chunks_can_include_internal(
    monkeypatch,
) -> None:
    """Authorized searches can request internal documentation."""
    connection = install_fake_database(
        monkeypatch,
        [
            result_row(
                source_path="internal_only/merge.md",
                access_level="internal",
            )
        ],
    )

    results = retrieve_chunks(
        query="How do I use the merge workflow?",
        include_internal=True,
        embedding_provider=FakeEmbeddingProvider(),
    )

    parameters = connection.cursor_instance.parameters

    assert parameters[1] is True
    assert results[0].access_level == "internal"


def test_retrieve_chunks_rejects_wrong_vector_dimension(
    monkeypatch,
) -> None:
    """Query vectors must match the database vector dimension."""
    install_fake_database(
        monkeypatch,
        [],
    )

    with pytest.raises(
        RetrievalError,
        match="unexpected dimension",
    ):
        retrieve_chunks(
            query="Which tool detects plasmids?",
            embedding_provider=FakeEmbeddingProvider(
                dimension=128
            ),
        )


class FailingEmbeddingProvider:
    """Embedding provider that raises during inference."""

    def embed_query(
        self,
        query: str,
    ) -> list:
        """Simulate a model failure."""
        raise RuntimeError("Model inference failed")


def test_retrieve_chunks_wraps_embedding_failure(
    monkeypatch,
) -> None:
    """Embedding failures are converted into RetrievalError."""
    install_fake_database(
        monkeypatch,
        [],
    )

    with pytest.raises(
        RetrievalError,
        match="query embedding",
    ):
        retrieve_chunks(
            query="Which tool detects plasmids?",
            embedding_provider=FailingEmbeddingProvider(),
        )
