#!/usr/bin/env python3

"""Tests for documentation chunk generation."""

from ingestion.chunking import (
    build_chunk_key,
    chunk_document,
    split_section_content,
)
from ingestion.markdown import parse_markdown


def test_short_section_produces_one_chunk() -> None:
    """A section below the target size remains intact."""
    document = parse_markdown(
        content="""
# MobSuite

### What does it do?

MobSuite detects and types plasmids in draft assemblies.
""",
        source_path="analysis/mobsuite.md",
    )

    chunks = chunk_document(
        document=document,
        target_chars=500,
        max_chars=700,
    )

    assert len(chunks) == 1
    assert chunks[0].content == (
        "MobSuite detects and types plasmids in draft assemblies."
    )
    assert chunks[0].split_section is False


def test_long_section_produces_multiple_chunks() -> None:
    """A large section is split at paragraph boundaries."""
    document = parse_markdown(
        content="""
# Tool

### Description

First paragraph contains useful introductory information.

Second paragraph contains more detailed configuration information.

Third paragraph contains troubleshooting information.
""",
        source_path="analysis/tool.md",
    )

    chunks = chunk_document(
        document=document,
        target_chars=80,
        max_chars=100,
    )

    assert len(chunks) >= 2
    assert all(chunk.split_section for chunk in chunks)

    combined = "\n\n".join(
        chunk.content
        for chunk in chunks
    )

    assert "First paragraph" in combined
    assert "Second paragraph" in combined
    assert "Third paragraph" in combined


def test_chunk_metadata_is_retained() -> None:
    """Chunks retain document, category, and heading metadata."""
    document = parse_markdown(
        content="""
# GeneSeekr

### How do I use it?

#### Subject

Use `geneseekr`.
""",
        source_path="analysis/geneseekr.md",
    )

    chunk = chunk_document(document)[0]

    assert chunk.source_path == "analysis/geneseekr.md"
    assert chunk.category == "analysis"
    assert chunk.access_level == "standard"
    assert chunk.document_title == "GeneSeekr"
    assert chunk.heading == "Subject"
    assert chunk.heading_level == 4
    assert chunk.heading_path == (
        "GeneSeekr",
        "How do I use it?",
        "Subject",
    )


def test_embedding_content_contains_document_context() -> None:
    """Embedding text contains the title and heading hierarchy."""
    document = parse_markdown(
        content="""
# GeneSeekr

### How do I use it?

#### Subject

Use `geneseekr`.
""",
        source_path="analysis/geneseekr.md",
    )

    chunk = chunk_document(document)[0]

    assert "Document: GeneSeekr" in chunk.embedding_content
    assert (
        "Section: GeneSeekr > How do I use it? > Subject"
        in chunk.embedding_content
    )
    assert "Use `geneseekr`." in chunk.embedding_content


def test_internal_access_level_is_retained() -> None:
    """Internal-only metadata is copied into each chunk."""
    document = parse_markdown(
        content="""
# Merge

### Usage

Internal merge instructions.
""",
        source_path="internal_only/merge.md",
    )

    chunk = chunk_document(document)[0]

    assert chunk.category == "internal_only"
    assert chunk.access_level == "internal"


def test_code_fence_is_not_split() -> None:
    """A fenced code example remains in one chunk."""
    document = parse_markdown(
        content="""
# Example

### Configuration

```text
analysis=resfinder
cutoff=80
align=True
unique=True
```
""",
        source_path="analysis/example.md",
    )

    chunks = chunk_document(
        document=document,
        target_chars=30,
        max_chars=40,
    )

    assert len(chunks) == 1
    assert chunks[0].content.startswith("```text")
    assert chunks[0].content.endswith("```")
    assert "analysis=resfinder" in chunks[0].content


def test_oversized_line_is_split_without_losing_words() -> None:
    """Long prose lines are divided without losing content."""
    words = [
        f"word{number}"
        for number in range(30)
    ]
    content = " ".join(words)

    portions = split_section_content(
        content=content,
        target_chars=60,
        max_chars=70,
    )

    reconstructed_words = " ".join(portions).split()

    assert reconstructed_words == words
    assert all(len(portion) <= 70 for portion in portions)


def test_chunk_keys_are_unique_within_document() -> None:
    """Every generated chunk has a unique key."""
    document = parse_markdown(
        content="""
# Tool

### First Section

First section content.

### Second Section

Second section content.
""",
        source_path="analysis/tool.md",
    )

    chunks = chunk_document(document)
    keys = [
        chunk.chunk_key
        for chunk in chunks
    ]

    assert len(keys) == len(set(keys))


def test_chunk_key_is_deterministic() -> None:
    """The same inputs create the same chunk key."""
    first_key = build_chunk_key(
        source_path="analysis/mobsuite.md",
        heading_path=(
            "MobSuite",
            "What does it do?",
        ),
        section_index=0,
        chunk_index=0,
    )

    second_key = build_chunk_key(
        source_path="analysis/mobsuite.md",
        heading_path=(
            "MobSuite",
            "What does it do?",
        ),
        section_index=0,
        chunk_index=0,
    )

    assert first_key == second_key


def test_content_checksum_is_populated() -> None:
    """Generated chunks contain SHA-256 content checksums."""
    document = parse_markdown(
        content="""
# Tool

### Description

Some useful content.
""",
        source_path="analysis/tool.md",
    )

    chunk = chunk_document(document)[0]

    assert len(chunk.content_checksum) == 64
    assert all(
        character in "0123456789abcdef"
        for character in chunk.content_checksum
    )


def test_invalid_chunk_sizes_are_rejected() -> None:
    """Invalid target and maximum sizes raise clear errors."""
    try:
        split_section_content(
            content="Some content.",
            target_chars=200,
            max_chars=100,
        )
    except ValueError as exc:
        assert "target_chars" in str(exc)
    else:
        raise AssertionError("Expected ValueError was not raised")

