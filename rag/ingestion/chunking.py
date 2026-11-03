#!/usr/bin/env python3

"""Chunk generation for parsed RedmineAssistant documentation."""

from dataclasses import dataclass
import hashlib
import re

from ingestion.markdown import (
    ParsedDocument,
    ParsedSection,
)


DEFAULT_MAX_CHARS = 1800
DEFAULT_TARGET_CHARS = 1400

FENCE_START_PATTERN = re.compile(
    r"^[ \t]{0,3}(`{3,}|~{3,})",
)


@dataclass(frozen=True)
class DocumentChunk:
    """A size-controlled retrieval unit from a parsed document."""

    chunk_key: str
    source_path: str
    category: str
    access_level: str
    document_title: str
    heading: str
    heading_level: int
    heading_path: tuple[str, ...]
    section_index: int
    chunk_index: int
    content: str
    embedding_content: str
    content_checksum: str
    split_section: bool

    @property
    def heading_path_text(self) -> str:
        """Return the section hierarchy as readable text."""
        return " > ".join(self.heading_path)

    @property
    def character_count(self) -> int:
        """Return the number of characters in the display content."""
        return len(self.content)


def content_checksum(content: str) -> str:
    """
    Calculate a stable SHA-256 checksum.

    Args:
        content: Text to hash.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def slugify(value: str) -> str:
    """
    Convert text into a stable key component.

    Args:
        value: Text to normalize.

    Returns:
        Lowercase ASCII-like slug.
    """
    normalized = value.casefold()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")

    return normalized or "section"


def build_chunk_key(
    source_path: str,
    heading_path: tuple[str, ...],
    section_index: int,
    chunk_index: int,
) -> str:
    """
    Create a stable, human-readable chunk key.

    Args:
        source_path: Relative Markdown source path.
        heading_path: Complete heading hierarchy.
        section_index: Zero-based section position in the document.
        chunk_index: Zero-based chunk position in the section.

    Returns:
        Unique chunk key.
    """
    heading_slug = slugify("-".join(heading_path))

    return (
        f"{source_path}::"
        f"{section_index:04d}::{heading_slug}::"
        f"{chunk_index:04d}"
    )


def build_embedding_content(
    document_title: str,
    heading_path: tuple[str, ...],
    content: str,
) -> str:
    """
    Add document and heading context to chunk content.

    Args:
        document_title: Parsed document title.
        heading_path: Complete section hierarchy.
        content: Clean display content.

    Returns:
        Context-rich text for embedding generation.
    """
    section_name = " > ".join(heading_path)

    return (
        f"Document: {document_title}\n"
        f"Section: {section_name}\n\n"
        f"{content.strip()}"
    )


def is_fenced_block(block: str) -> bool:
    """
    Determine whether a logical block begins with a code fence.

    Args:
        block: Logical Markdown block.

    Returns:
        True when the block starts with a Markdown code fence.
    """
    first_nonempty_line = next(
        (
            line
            for line in block.splitlines()
            if line.strip()
        ),
        "",
    )

    return FENCE_START_PATTERN.match(first_nonempty_line) is not None


def split_logical_blocks(content: str) -> list[str]:
    """
    Split Markdown into paragraph-like blocks.

    Blank lines create block boundaries except while inside a fenced code
    block. Fenced code blocks are kept intact.

    Args:
        content: Markdown section content.

    Returns:
        Ordered logical blocks.
    """
    blocks: list[str] = []
    current_lines: list[str] = []

    inside_fence = False
    fence_character = ""
    fence_length = 0

    def flush_current_block() -> None:
        block = "\n".join(current_lines).strip()

        if block:
            blocks.append(block)

        current_lines.clear()

    for line in content.splitlines():
        fence_match = FENCE_START_PATTERN.match(line)

        if fence_match:
            marker = fence_match.group(1)
            marker_character = marker[0]

            if not inside_fence:
                inside_fence = True
                fence_character = marker_character
                fence_length = len(marker)
            elif (
                marker_character == fence_character
                and len(marker) >= fence_length
            ):
                inside_fence = False
                fence_character = ""
                fence_length = 0

            current_lines.append(line)
            continue

        if not inside_fence and not line.strip():
            flush_current_block()
            continue

        current_lines.append(line)

    flush_current_block()

    return blocks


def split_text_by_words(
    text: str,
    max_chars: int,
) -> list[str]:
    """
    Split oversized prose without breaking individual words.

    Args:
        text: Text requiring subdivision.
        max_chars: Maximum preferred chunk size.

    Returns:
        Size-controlled text portions.
    """
    words = text.split()

    if not words:
        return []

    portions: list[str] = []
    current_words: list[str] = []

    for word in words:
        candidate = " ".join(current_words + [word])

        if current_words and len(candidate) > max_chars:
            portions.append(" ".join(current_words))
            current_words = [word]
        else:
            current_words.append(word)

    if current_words:
        portions.append(" ".join(current_words))

    return portions


def split_oversized_block(
    block: str,
    max_chars: int,
) -> list[str]:
    """
    Split an oversized non-code block by line and then by word.

    Fenced code blocks are deliberately left intact, even when they exceed
    the preferred size limit, because cutting code examples can change their
    meaning.

    Args:
        block: Logical Markdown block.
        max_chars: Maximum preferred chunk size.

    Returns:
        One or more block portions.
    """
    if len(block) <= max_chars:
        return [block]

    if is_fenced_block(block):
        return [block]

    portions: list[str] = []
    current_lines: list[str] = []

    def flush_current_lines() -> None:
        combined = "\n".join(current_lines).strip()

        if combined:
            portions.append(combined)

        current_lines.clear()

    for line in block.splitlines():
        if len(line) > max_chars:
            flush_current_lines()
            portions.extend(
                split_text_by_words(
                    text=line,
                    max_chars=max_chars,
                )
            )
            continue

        candidate = "\n".join(current_lines + [line]).strip()

        if current_lines and len(candidate) > max_chars:
            flush_current_lines()

        current_lines.append(line)

    flush_current_lines()

    return portions


def split_section_content(
    content: str,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[str]:
    """
    Divide section content into retrieval-sized portions.

    Paragraph and list boundaries are preferred. Code fences remain intact.

    Args:
        content: Parsed Markdown section content.
        target_chars: Preferred chunk size.
        max_chars: Maximum preferred chunk size.

    Returns:
        Ordered chunk contents.

    Raises:
        ValueError: If the configured sizes are invalid.
    """
    if target_chars <= 0:
        raise ValueError("target_chars must be greater than zero")

    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    if target_chars > max_chars:
        raise ValueError(
            "target_chars cannot be greater than max_chars"
        )

    logical_blocks = split_logical_blocks(content)
    expanded_blocks: list[str] = []

    for block in logical_blocks:
        expanded_blocks.extend(
            split_oversized_block(
                block=block,
                max_chars=max_chars,
            )
        )

    chunks: list[str] = []
    current_blocks: list[str] = []

    def flush_current_chunk() -> None:
        combined = "\n\n".join(current_blocks).strip()

        if combined:
            chunks.append(combined)

        current_blocks.clear()

    for block in expanded_blocks:
        if not current_blocks:
            current_blocks.append(block)
            continue

        candidate = "\n\n".join(
            current_blocks + [block]
        ).strip()

        if len(candidate) <= target_chars:
            current_blocks.append(block)
            continue

        flush_current_chunk()
        current_blocks.append(block)

    flush_current_chunk()

    return chunks


def chunk_section(
    document: ParsedDocument,
    section: ParsedSection,
    section_index: int,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[DocumentChunk]:
    """
    Convert one parsed section into retrieval chunks.

    Args:
        document: Parent parsed document.
        section: Parsed section to divide.
        section_index: Zero-based section index.
        target_chars: Preferred chunk size.
        max_chars: Maximum preferred chunk size.

    Returns:
        Retrieval chunks.
    """
    contents = split_section_content(
        content=section.content,
        target_chars=target_chars,
        max_chars=max_chars,
    )

    split_section = len(contents) > 1
    chunks: list[DocumentChunk] = []

    for chunk_index, chunk_content in enumerate(contents):
        chunks.append(
            DocumentChunk(
                chunk_key=build_chunk_key(
                    source_path=document.source_path,
                    heading_path=section.heading_path,
                    section_index=section_index,
                    chunk_index=chunk_index,
                ),
                source_path=document.source_path,
                category=document.category,
                access_level=document.access_level,
                document_title=document.title,
                heading=section.heading,
                heading_level=section.heading_level,
                heading_path=section.heading_path,
                section_index=section_index,
                chunk_index=chunk_index,
                content=chunk_content,
                embedding_content=build_embedding_content(
                    document_title=document.title,
                    heading_path=section.heading_path,
                    content=chunk_content,
                ),
                content_checksum=content_checksum(chunk_content),
                split_section=split_section,
            )
        )

    return chunks


def chunk_document(
    document: ParsedDocument,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[DocumentChunk]:
    """
    Convert all content-bearing sections in a document into chunks.

    Args:
        document: Parsed Markdown document.
        target_chars: Preferred chunk size.
        max_chars: Maximum preferred chunk size.

    Returns:
        Ordered document chunks.
    """
    chunks: list[DocumentChunk] = []

    for section_index, section in enumerate(document.sections):
        chunks.extend(
            chunk_section(
                document=document,
                section=section,
                section_index=section_index,
                target_chars=target_chars,
                max_chars=max_chars,
            )
        )

    return chunks
