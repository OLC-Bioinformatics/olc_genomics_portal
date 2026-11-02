#!/usr/bin/env python3

"""Discovery of Markdown documents for the RedmineAssistant index."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config import settings


EXCLUDED_DIRECTORIES = frozenset({
    "tutorials",
})


@dataclass(frozen=True)
class DiscoveredDocument:
    """A Markdown document selected for indexing."""

    absolute_path: Path
    relative_path: Path
    category: str

    @property
    def source_path(self) -> str:
        """
        Return the platform-independent source path stored in the index.

        Returns:
            POSIX-style path relative to the documentation root.
        """
        return self.relative_path.as_posix()


def documentation_root() -> Path:
    """
    Return and validate the configured documentation root.

    Returns:
        Resolved documentation root.

    Raises:
        FileNotFoundError: If the configured path does not exist.
        NotADirectoryError: If the configured path is not a directory.
    """
    root = Path(settings.documentation_root).resolve()

    if not root.exists():
        raise FileNotFoundError(
            f"Documentation root does not exist: {root}"
        )

    if not root.is_dir():
        raise NotADirectoryError(
            f"Documentation root is not a directory: {root}"
        )

    return root


def is_excluded(
    relative_path: Path,
    excluded_directories: Iterable[str] = EXCLUDED_DIRECTORIES,
) -> bool:
    """
    Determine whether a document belongs to an excluded directory.

    Args:
        relative_path: Path relative to the documentation root.
        excluded_directories: Directory names excluded from indexing.

    Returns:
        True when any parent directory is excluded.
    """
    excluded = set(excluded_directories)

    return any(
        path_part in excluded
        for path_part in relative_path.parts[:-1]
    )


def document_category(relative_path: Path) -> str:
    """
    Determine the category for a discovered document.

    Base-level Markdown files use the category ``base``. Files in a
    subdirectory use their first directory component.

    Args:
        relative_path: Path relative to the documentation root.

    Returns:
        Document category.
    """
    if len(relative_path.parts) == 1:
        return "base"

    return relative_path.parts[0]


def discover_markdown_documents(
    root: Path | None = None,
) -> list:
    """
    Find Markdown documents eligible for indexing.

    Discovery includes Markdown documents recursively under the configured
    documentation root while excluding documents in explicitly excluded
    directories.

    Args:
        root: Optional documentation root override, primarily for tests.

    Returns:
        Discovered documents sorted by relative source path.
    """
    selected_root = (
        root.resolve()
        if root is not None
        else documentation_root()
    )

    documents: list[DiscoveredDocument] = []

    for absolute_path in selected_root.rglob("*.md"):
        if not absolute_path.is_file():
            continue

        relative_path = absolute_path.relative_to(selected_root)

        if is_excluded(relative_path):
            continue

        documents.append(
            DiscoveredDocument(
                absolute_path=absolute_path,
                relative_path=relative_path,
                category=document_category(relative_path),
            )
        )

    return sorted(
        documents,
        key=lambda document: document.source_path.casefold(),
    )


def discovery_summary(
    documents: list[DiscoveredDocument],
) -> dict[str, object]:
    """
    Summarize discovered documentation.

    Args:
        documents: Discovered Markdown documents.

    Returns:
        Total count and counts grouped by category.
    """
    category_counts = Counter(
        document.category
        for document in documents
    )

    return {
        "document_count": len(documents),
        "categories": dict(sorted(category_counts.items())),
        "excluded_directories": sorted(EXCLUDED_DIRECTORIES),
    }

