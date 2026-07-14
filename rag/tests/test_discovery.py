#!/usr/bin/env python3

"""Tests for Markdown document discovery."""

from pathlib import Path

from ingestion.discovery import (
    discover_markdown_documents,
    discovery_summary,
)


def write_document(path: Path, content: str = "# Test\n") -> None:
    """Create a Markdown test document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discovery_includes_expected_directories(tmp_path: Path) -> None:
    """Eligible Markdown documents should be discovered."""
    write_document(tmp_path / "index.md")
    write_document(tmp_path / "analysis" / "geneseekr.md")
    write_document(tmp_path / "data" / "external_retrieve.md")
    write_document(tmp_path / "internal_only" / "merge.md")

    documents = discover_markdown_documents(tmp_path)

    assert [
        document.source_path
        for document in documents
    ] == [
        "analysis/geneseekr.md",
        "data/external_retrieve.md",
        "index.md",
        "internal_only/merge.md",
    ]


def test_discovery_excludes_tutorials(tmp_path: Path) -> None:
    """Documents under tutorials should not be indexed."""
    write_document(tmp_path / "index.md")
    write_document(tmp_path / "tutorials" / "create_pages.md")
    write_document(
        tmp_path
        / "tutorials"
        / "nested"
        / "other_tutorial.md"
    )

    documents = discover_markdown_documents(tmp_path)

    assert [
        document.source_path
        for document in documents
    ] == [
        "index.md",
    ]


def test_discovery_ignores_non_markdown_files(tmp_path: Path) -> None:
    """Only Markdown documents should be returned."""
    write_document(tmp_path / "index.md")
    (tmp_path / "image.png").write_bytes(b"not-an-image")
    (tmp_path / "notes.txt").write_text(
        "Not Markdown",
        encoding="utf-8",
    )

    documents = discover_markdown_documents(tmp_path)

    assert len(documents) == 1
    assert documents[0].source_path == "index.md"


def test_discovery_assigns_categories(tmp_path: Path) -> None:
    """Base and subdirectory documents should be categorized."""
    write_document(tmp_path / "index.md")
    write_document(tmp_path / "analysis" / "geneseekr.md")
    write_document(tmp_path / "analysis" / "mobsuite.md")
    write_document(tmp_path / "internal_only" / "merge.md")

    documents = discover_markdown_documents(tmp_path)
    summary = discovery_summary(documents)

    assert summary["document_count"] == 4
    assert summary["categories"] == {
        "analysis": 2,
        "base": 1,
        "internal_only": 1,
    }

