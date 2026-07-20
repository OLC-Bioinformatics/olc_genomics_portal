#!/usr/bin/env python3

"""Semantic retrieval from the RedmineAssistant pgvector index."""

# Standard library imports
from dataclasses import dataclass
import logging
from typing import Any

# Third-party imports
from pgvector.psycopg import register_vector

# Local imports
from config import settings
from database import database_connection
from embeddings import (
    EmbeddingService,
    embedding_service,
)


LOGGER = logging.getLogger("redmine-assistant-retrieval")


class RetrievalError(RuntimeError):
    """Raised when query validation or semantic retrieval fails."""


@dataclass(frozen=True)
class SearchResult:
    """One documentation chunk returned by semantic search."""

    rank: int
    chunk_key: str
    source_path: str
    source_url: str | None
    document_title: str
    heading_path: str
    content: str
    score: float
    access_level: str

    @property
    def score_percentage(self) -> float:
        """Return the similarity score as a percentage."""
        return self.score * 100.0


def validate_query(query: str) -> str:
    """
    Validate and normalize a search query.

    Args:
        query: User-supplied search text.

    Returns:
        Query with surrounding whitespace removed.

    Raises:
        RetrievalError: If the query is not a string or is blank.
    """
    if not isinstance(query, str):
        raise RetrievalError(
            "Search query must be a string"
        )

    normalized_query = query.strip()

    if not normalized_query:
        raise RetrievalError("Search query cannot be blank")

    if len(normalized_query) > settings.max_query_chars:
        raise RetrievalError(
            f"Search query cannot exceed {settings.max_query_chars} characters"
        )

    return normalized_query


def validate_limit(limit: int | None) -> int:
    """
    Validate the requested result limit.

    Args:
        limit: Requested number of search results.

    Returns:
        Validated result limit.

    Raises:
        RetrievalError: If the limit is not a valid integer or is outside
            the configured range.
    """
    if limit is None:
        return settings.top_k

    if isinstance(limit, bool) or not isinstance(limit, int):
        raise RetrievalError(
            "Search result limit must be an integer"
        )

    if limit < 1:
        raise RetrievalError(
            "Search result limit must be at least 1"
        )

    if limit > settings.max_top_k:
        raise RetrievalError(
            "Search result limit cannot exceed "
            f"{settings.max_top_k}"
        )

    return limit


def row_to_search_result(
    row: dict[str, Any],
    rank: int,
) -> SearchResult:
    """
    Convert a PostgreSQL result row into a SearchResult.

    Args:
        row: Database row returned by the search query.
        rank: One-based search-result rank.

    Returns:
        Structured search result.
    """
    return SearchResult(
        rank=rank,
        chunk_key=str(row["chunk_key"]),
        source_path=str(row["source_path"]),
        source_url=(
            str(row["source_url"])
            if row["source_url"] is not None
            else None
        ),
        document_title=str(row["document_title"]),
        heading_path=str(row["heading_path"]),
        content=str(row["content"]),
        score=float(row["score"]),
        access_level=str(row["access_level"]),
    )


def retrieve_chunks(
    query: str,
    limit: int | None = None,
    include_internal: bool = False,
    embedding_provider: EmbeddingService = embedding_service,
) -> list[SearchResult]:
    """
    Retrieve documentation chunks related to a query.

    Exact cosine-distance search is used. Internal documentation is excluded
    unless explicitly requested.

    Args:
        query: User search question.
        limit: Maximum number of results.
        include_internal: Include internal-only documentation when True.
        embedding_provider: Service used to generate the query vector.

    Returns:
        Results ordered by decreasing cosine similarity.

    Raises:
        RetrievalError: If the query, limit, embedding operation, or
            database search fails.
    """
    normalized_query = validate_query(query)
    validated_limit = validate_limit(limit)

    try:
        query_embedding = embedding_provider.embed_query(
            normalized_query
        )
    except Exception as exc:
        raise RetrievalError(
            "Failed to generate the search-query embedding"
        ) from exc

    if len(query_embedding) != settings.embedding_dimension:
        raise RetrievalError(
            "Query embedding has an unexpected dimension: "
            f"expected={settings.embedding_dimension}, "
            f"actual={len(query_embedding)}"
        )

    try:
        with database_connection() as connection:
            register_vector(connection)

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH query_vector AS (
                        SELECT %s::vector AS embedding
                    )
                    SELECT
                        dc.chunk_key,
                        d.source_path,
                        dc.source_url,
                        d.title AS document_title,
                        dc.heading_path,
                        dc.content,
                        COALESCE(
                            dc.metadata->>'access_level',
                            d.metadata->>'access_level',
                            'standard'
                        ) AS access_level,
                        1 - (
                            dc.embedding
                            <=>
                            query_vector.embedding
                        ) AS score
                    FROM document_chunks AS dc
                    JOIN documents AS d
                        ON d.id = dc.document_id
                    CROSS JOIN query_vector
                    WHERE dc.embedding IS NOT NULL
                      AND (
                          %s
                          OR COALESCE(
                              dc.metadata->>'access_level',
                              d.metadata->>'access_level',
                              'standard'
                          ) = 'standard'
                      )
                    ORDER BY
                        dc.embedding <=> query_vector.embedding,
                        dc.id
                    LIMIT %s
                    """,
                    (
                        query_embedding,
                        include_internal,
                        validated_limit,
                    ),
                )

                rows = cursor.fetchall()

    except Exception as exc:
        LOGGER.exception(
            "Semantic retrieval failed"
        )

        raise RetrievalError(
            "Failed to search the documentation index"
        ) from exc

    return [
        row_to_search_result(
            row=row,
            rank=rank,
        )
        for rank, row in enumerate(rows, start=1)
    ]
