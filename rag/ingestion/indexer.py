#!/usr/bin/env python3

"""Idempotent documentation indexing for RedmineAssistant."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.types.json import Jsonb

from config import settings
from database import database_connection
from embeddings import EmbeddingService, embedding_service
from ingestion.chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_TARGET_CHARS,
    DocumentChunk,
    chunk_document,
)
from ingestion.discovery import (
    DiscoveredDocument,
    discover_markdown_documents,
    documentation_root,
)
from ingestion.markdown import parse_markdown_file


LOGGER = logging.getLogger("redmine-assistant-indexer")


@dataclass(frozen=True)
class PreparedDocument:
    """A parsed, chunked, and embedded document ready for persistence."""

    discovered_document: DiscoveredDocument
    title: str
    source_checksum: str
    modified_at: datetime
    chunks: tuple[DocumentChunk, ...]
    embeddings: tuple[tuple[float, ...], ...]


@dataclass
class IndexSummary:
    """Summary of one indexing operation."""

    discovered: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    chunks_embedded: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def failure_count(self) -> int:
        """Return the number of failed documents."""
        return len(self.failures)

    @property
    def successful(self) -> bool:
        """Return whether indexing completed without document failures."""
        return not self.failures


def file_checksum(path: Path) -> str:
    """
    Calculate the SHA-256 checksum of a source file.

    Args:
        path: File to hash.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    digest = hashlib.sha256()

    with path.open("rb") as source_file:
        for block in iter(
            lambda: source_file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def file_modified_at(path: Path) -> datetime:
    """
    Return a file modification timestamp in UTC.

    Args:
        path: Source file.

    Returns:
        Timezone-aware UTC modification timestamp.
    """
    return datetime.fromtimestamp(
        path.stat().st_mtime,
        tz=timezone.utc,
    )


def current_index_configuration() -> dict[str, str]:
    """
    Return the configuration used to generate the current index.

    Returns:
        String metadata suitable for the index_metadata table.
    """
    return {
        "embedding_model": settings.embedding_model,
        "embedding_model_revision": (
            settings.embedding_model_revision
        ),
        "embedding_dimension": str(
            settings.embedding_dimension
        ),
        "embedding_normalize": str(
            settings.embedding_normalize
        ).lower(),
        "chunk_target_chars": str(DEFAULT_TARGET_CHARS),
        "chunk_max_chars": str(DEFAULT_MAX_CHARS),
    }


def load_index_metadata(
    connection: Connection,
) -> dict[str, str]:
    """
    Load current index metadata.

    Args:
        connection: Active PostgreSQL connection.

    Returns:
        Metadata key-value pairs.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT key, value
            FROM index_metadata
            """
        )
        rows = cursor.fetchall()

    return {
        row["key"]: row["value"]
        for row in rows
    }


def validate_index_configuration(
    connection: Connection,
) -> None:
    """
    Prevent incompatible embedding configurations from being mixed.

    An empty index can adopt the current configuration. A populated index
    must use the same model, revision, dimensions, normalization, and
    chunk-size configuration.

    Args:
        connection: Active PostgreSQL connection.

    Raises:
        RuntimeError: If existing vectors use incompatible settings.
    """
    expected = current_index_configuration()
    existing = load_index_metadata(connection)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS chunk_count
            FROM document_chunks
            """
        )
        chunk_count = cursor.fetchone()["chunk_count"]

    if chunk_count == 0 or not existing:
        return

    mismatches = []

    for key, expected_value in expected.items():
        existing_value = existing.get(key)

        if existing_value != expected_value:
            mismatches.append(
                f"{key}: existing={existing_value!r}, "
                f"configured={expected_value!r}"
            )

    if mismatches:
        mismatch_text = "; ".join(mismatches)

        raise RuntimeError(
            "The existing vector index was created with an "
            "incompatible configuration. Rebuild the index before "
            f"continuing. Differences: {mismatch_text}"
        )


def store_index_metadata(
    connection: Connection,
    document_count: int,
) -> None:
    """
    Store the configuration and completion time of the index.

    Args:
        connection: Active PostgreSQL connection.
        document_count: Number of discovered documents.
    """
    metadata = current_index_configuration()
    metadata["documentation_git_commit"] = os.getenv(
        "DOCUMENTATION_GIT_COMMIT",
        "unknown",
    )
    metadata["documentation_document_count"] = str(
        document_count
    )
    metadata["last_successful_index_at"] = (
        datetime.now(timezone.utc).isoformat()
    )

    with connection.cursor() as cursor:
        for key, value in metadata.items():
            cursor.execute(
                """
                INSERT INTO index_metadata (
                    key,
                    value,
                    updated_at
                )
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key)
                DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )


def existing_document_checksums(
    connection: Connection,
) -> dict[str, str]:
    """
    Return source paths and checksums currently stored in PostgreSQL.

    Args:
        connection: Active PostgreSQL connection.

    Returns:
        Mapping of relative source paths to content checksums.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT source_path, content_checksum
            FROM documents
            """
        )
        rows = cursor.fetchall()

    return {
        row["source_path"]: row["content_checksum"]
        for row in rows
    }


def prepare_document(
    discovered_document: DiscoveredDocument,
    source_checksum: str,
    embedding_provider: EmbeddingService,
) -> PreparedDocument:
    """
    Parse, chunk, and embed one source document.

    This function performs all potentially failing parsing and model work
    before making database changes.

    Args:
        discovered_document: Source document selected for indexing.
        source_checksum: SHA-256 checksum of the source file.
        embedding_provider: Embedding service.

    Returns:
        Document prepared for database persistence.
    """
    parsed_document = parse_markdown_file(
        path=discovered_document.absolute_path,
        source_path=discovered_document.source_path,
    )

    chunks = chunk_document(parsed_document)

    embedding_texts = [
        chunk.embedding_content
        for chunk in chunks
    ]

    embeddings = embedding_provider.embed_documents(
        embedding_texts
    )

    if len(chunks) != len(embeddings):
        raise RuntimeError(
            "Chunk and embedding counts do not match for "
            f"{discovered_document.source_path}"
        )

    return PreparedDocument(
        discovered_document=discovered_document,
        title=parsed_document.title,
        source_checksum=source_checksum,
        modified_at=file_modified_at(
            discovered_document.absolute_path
        ),
        chunks=tuple(chunks),
        embeddings=tuple(
            tuple(vector)
            for vector in embeddings
        ),
    )


def upsert_document(
    connection: Connection,
    prepared_document: PreparedDocument,
) -> int:
    """
    Insert or update one document and return its database ID.

    Args:
        connection: Active PostgreSQL connection.
        prepared_document: Prepared source document.

    Returns:
        PostgreSQL document ID.
    """
    source_path = (
        prepared_document.discovered_document.source_path
    )

    metadata = {
        "category": (
            prepared_document.discovered_document.category
        ),
        "access_level": (
            "internal"
            if prepared_document.discovered_document.category
            == "internal_only"
            else "standard"
        ),
    }

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO documents (
                source_path,
                title,
                content_checksum,
                modified_at,
                indexed_at,
                metadata
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                CURRENT_TIMESTAMP,
                %s
            )
            ON CONFLICT (source_path)
            DO UPDATE SET
                title = EXCLUDED.title,
                content_checksum = EXCLUDED.content_checksum,
                modified_at = EXCLUDED.modified_at,
                indexed_at = CURRENT_TIMESTAMP,
                metadata = EXCLUDED.metadata
            RETURNING id
            """,
            (
                source_path,
                prepared_document.title,
                prepared_document.source_checksum,
                prepared_document.modified_at,
                Jsonb(metadata),
            ),
        )

        row = cursor.fetchone()

    return int(row["id"])


def replace_document_chunks(
    connection: Connection,
    document_id: int,
    prepared_document: PreparedDocument,
) -> None:
    """
    Replace all chunks belonging to one document.

    Deletion and insertion occur in the caller's transaction. If insertion
    fails, PostgreSQL rolls back and preserves the prior valid chunks.

    Args:
        connection: Active PostgreSQL connection.
        document_id: Parent document ID.
        prepared_document: Prepared source document.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM document_chunks
            WHERE document_id = %s
            """,
            (document_id,),
        )

        for chunk, embedding in zip(
            prepared_document.chunks,
            prepared_document.embeddings,
            strict=True,
        ):
            metadata = {
                "category": chunk.category,
                "access_level": chunk.access_level,
                "document_title": chunk.document_title,
                "heading": chunk.heading,
                "heading_level": chunk.heading_level,
                "heading_path": list(chunk.heading_path),
                "section_index": chunk.section_index,
                "chunk_index": chunk.chunk_index,
                "split_section": chunk.split_section,
            }

            cursor.execute(
                """
                INSERT INTO document_chunks (
                    document_id,
                    chunk_key,
                    chunk_order,
                    heading_path,
                    content,
                    embedding_content,
                    content_checksum,
                    source_url,
                    metadata,
                    embedding,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    document_id,
                    chunk.chunk_key,
                    chunk.section_index * 1000 + chunk.chunk_index,
                    chunk.heading_path_text,
                    chunk.content,
                    chunk.embedding_content,
                    chunk.content_checksum,
                    chunk.source_path,
                    Jsonb(metadata),
                    list(embedding),
                ),
            )


def persist_prepared_document(
    connection: Connection,
    prepared_document: PreparedDocument,
) -> None:
    """
    Persist one prepared document and all of its chunks atomically.

    Args:
        connection: Active PostgreSQL connection.
        prepared_document: Document ready for persistence.
    """
    document_id = upsert_document(
        connection,
        prepared_document,
    )

    replace_document_chunks(
        connection=connection,
        document_id=document_id,
        prepared_document=prepared_document,
    )


def remove_deleted_documents(
    connection: Connection,
    discovered_source_paths: set[str],
) -> int:
    """
    Remove indexed documents that no longer exist in the documentation.

    Child chunks are removed by the ON DELETE CASCADE relationship.

    Args:
        connection: Active PostgreSQL connection.
        discovered_source_paths: Current source paths.

    Returns:
        Number of removed documents.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT source_path
            FROM documents
            """
        )
        indexed_paths = {
            row["source_path"]
            for row in cursor.fetchall()
        }

    removed_paths = indexed_paths - discovered_source_paths

    if not removed_paths:
        return 0

    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM documents
            WHERE source_path = ANY(%s)
            """,
            (list(removed_paths),),
        )

    return len(removed_paths)


def index_documentation(
    embedding_provider: EmbeddingService = embedding_service,
) -> IndexSummary:
    """
    Index all changed documentation into PostgreSQL.

    Unchanged documents are skipped. A changed document is fully parsed,
    chunked, and embedded before its previous database representation is
    replaced.

    Args:
        embedding_provider: Embedding service used for model inference.

    Returns:
        Indexing summary.

    Raises:
        RuntimeError: If the existing index configuration is incompatible.
    """
    root = documentation_root()
    discovered_documents = discover_markdown_documents(root)

    summary = IndexSummary(
        discovered=len(discovered_documents)
    )

    with database_connection() as connection:
        register_vector(connection)
        validate_index_configuration(connection)

        stored_checksums = existing_document_checksums(
            connection
        )

    discovered_source_paths = {
        document.source_path
        for document in discovered_documents
    }

    for discovered_document in discovered_documents:
        source_path = discovered_document.source_path

        try:
            checksum = file_checksum(
                discovered_document.absolute_path
            )

            existing_checksum = stored_checksums.get(source_path)

            if existing_checksum == checksum:
                summary.unchanged += 1
                continue

            prepared_document = prepare_document(
                discovered_document=discovered_document,
                source_checksum=checksum,
                embedding_provider=embedding_provider,
            )

            with database_connection() as connection:
                register_vector(connection)

                persist_prepared_document(
                    connection=connection,
                    prepared_document=prepared_document,
                )

            if existing_checksum is None:
                summary.added += 1
            else:
                summary.updated += 1

            summary.chunks_embedded += len(
                prepared_document.chunks
            )

        except Exception as exc:
            LOGGER.exception(
                "Failed to index %s",
                source_path,
            )
            summary.failures.append(
                (source_path, str(exc))
            )

    if summary.successful:
        with database_connection() as connection:
            register_vector(connection)

            summary.removed = remove_deleted_documents(
                connection=connection,
                discovered_source_paths=(
                    discovered_source_paths
                ),
            )

            store_index_metadata(
                connection=connection,
                document_count=len(discovered_documents),
            )
    else:
        LOGGER.warning(
            "Skipping deleted-document cleanup and index metadata "
            "update because one or more documents failed"
        )

    return summary
