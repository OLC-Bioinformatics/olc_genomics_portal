#!/usr/bin/env python3

"""Tests for documentation-index preparation."""

from pathlib import Path

from ingestion.indexer import (
    current_index_configuration,
    file_checksum,
    file_modified_at,
    prepare_document,
)
from ingestion.discovery import DiscoveredDocument


class FakeEmbeddingProvider:
    """Deterministic embedding provider for indexer tests."""

    def __init__(self) -> None:
        """Initialize the test provider."""
        self.received_texts: list[str] = []

    def embed_documents(
        self,
        texts,
    ) -> list[list[float]]:
        """Return one deterministic vector per input."""
        self.received_texts.extend(texts)

        vectors = []

        for index, _ in enumerate(texts, start=1):
            vector = [0.0] * 384
            vector[0] = float(index)
            vectors.append(vector)

        return vectors


def test_file_checksum_is_stable(tmp_path: Path) -> None:
    """Unchanged content produces the same checksum."""
    source = tmp_path / "tool.md"
    source.write_text(
        "# Tool\n\nSome content.\n",
        encoding="utf-8",
    )

    first_checksum = file_checksum(source)
    second_checksum = file_checksum(source)

    assert first_checksum == second_checksum
    assert len(first_checksum) == 64


def test_file_checksum_changes_with_content(
    tmp_path: Path,
) -> None:
    """Changed source content produces a new checksum."""
    source = tmp_path / "tool.md"
    source.write_text(
        "First version",
        encoding="utf-8",
    )

    first_checksum = file_checksum(source)

    source.write_text(
        "Second version",
        encoding="utf-8",
    )

    second_checksum = file_checksum(source)

    assert first_checksum != second_checksum


def test_file_modified_at_is_timezone_aware(
    tmp_path: Path,
) -> None:
    """File modification timestamps include timezone information."""
    source = tmp_path / "tool.md"
    source.write_text(
        "# Tool\n",
        encoding="utf-8",
    )

    modified_at = file_modified_at(source)

    assert modified_at.tzinfo is not None


def test_prepare_document_parses_chunks_and_embeds(
    tmp_path: Path,
) -> None:
    """Document preparation creates matching chunks and vectors."""
    source = tmp_path / "mobsuite.md"
    source.write_text(
        (
            "# MobSuite\n"
            "\n"
            "### What does it do?\n"
            "\n"
            "MobSuite detects plasmids.\n"
            "\n"
            "### How long does it take?\n"
            "\n"
            "Approximately one minute per assembly.\n"
        ),
        encoding="utf-8",
    )

    discovered = DiscoveredDocument(
        absolute_path=source,
        relative_path=Path("analysis/mobsuite.md"),
        category="analysis",
    )

    embedding_provider = FakeEmbeddingProvider()

    prepared = prepare_document(
        discovered_document=discovered,
        source_checksum=file_checksum(source),
        embedding_provider=embedding_provider,
    )

    assert prepared.title == "MobSuite"
    assert len(prepared.chunks) == 2
    assert len(prepared.embeddings) == 2
    assert len(prepared.embeddings[0]) == 384
    assert len(embedding_provider.received_texts) == 2

    assert (
        "Document: MobSuite"
        in embedding_provider.received_texts[0]
    )


def test_current_index_configuration_contains_model_data() -> None:
    """Index metadata records the embedding configuration."""
    configuration = current_index_configuration()

    assert configuration["embedding_dimension"] == "384"
    assert configuration["embedding_normalize"] == "true"
    assert "embedding_model" in configuration
    assert "embedding_model_revision" in configuration
    assert "chunk_target_chars" in configuration
    assert "chunk_max_chars" in configuration
