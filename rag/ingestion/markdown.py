#!/usr/bin/env python3

"""Markdown parsing for the RedmineAssistant documentation index."""

from dataclasses import dataclass
from html import unescape
from pathlib import Path
import re


HTML_COMMENT_PATTERN = re.compile(
    r"<!--.*?-->",
    flags=re.DOTALL,
)

DETAILS_TAG_PATTERN = re.compile(
    r"</?details\b[^>]*>",
    flags=re.IGNORECASE,
)

SUMMARY_PATTERN = re.compile(
    r"<summary\b[^>]*>(.*?)</summary>",
    flags=re.IGNORECASE | re.DOTALL,
)

HTML_TAG_PATTERN = re.compile(
    r"<[^>]+>",
)

ATX_HEADING_PATTERN = re.compile(
    r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$",
)

FENCE_PATTERN = re.compile(
    r"^[ \t]{0,3}(`{3,}|~{3,})",
)


@dataclass(frozen=True)
class ParsedSection:
    """A logical Markdown section associated with a heading hierarchy."""

    heading: str
    heading_level: int
    heading_path: tuple[str, ...]
    content: str

    @property
    def heading_path_text(self) -> str:
        """Return the heading hierarchy as readable text."""
        return " > ".join(self.heading_path)


@dataclass(frozen=True)
class ParsedDocument:
    """A parsed Markdown document."""

    source_path: str
    category: str
    access_level: str
    title: str
    sections: tuple[ParsedSection, ...]
    raw_content: str

    @property
    def section_count(self) -> int:
        """Return the number of parsed sections."""
        return len(self.sections)


@dataclass
class PendingSection:
    """Mutable section state used while parsing Markdown."""

    heading: str
    heading_level: int
    heading_path: tuple[str, ...]
    content_lines: list[str]


def normalize_heading(heading: str) -> str:
    """
    Normalize a Markdown heading.

    Inline HTML tags are removed, HTML entities are decoded, and surrounding
    whitespace is stripped.

    Args:
        heading: Raw heading text.

    Returns:
        Normalized heading text.
    """
    without_html = HTML_TAG_PATTERN.sub("", heading)
    decoded = unescape(without_html)

    return " ".join(decoded.split()).strip()


def normalize_summary(summary: str) -> str:
    """
    Convert HTML summary content into readable Markdown text.

    Args:
        summary: Content captured from an HTML summary element.

    Returns:
        A Markdown-formatted summary line.
    """
    without_html = HTML_TAG_PATTERN.sub("", summary)
    decoded = unescape(without_html)
    normalized = " ".join(decoded.split()).strip()

    if not normalized:
        return ""

    return f"**{normalized}**"


def remove_html_comments(content: str) -> str:
    """
    Remove HTML comments from Markdown content.

    Args:
        content: Raw Markdown.

    Returns:
        Markdown with HTML comments removed.
    """
    return HTML_COMMENT_PATTERN.sub("", content)


def normalize_details_blocks(content: str) -> str:
    """
    Remove details wrappers while retaining their visible content.

    Summary content is converted into a bold Markdown line so that the
    descriptive text remains available for indexing.

    Args:
        content: Markdown containing optional details and summary elements.

    Returns:
        Normalized Markdown.
    """

    def replace_summary(match: re.Match[str]) -> str:
        summary = normalize_summary(match.group(1))

        if not summary:
            return ""

        return f"\n{summary}\n"

    normalized = SUMMARY_PATTERN.sub(replace_summary, content)
    normalized = DETAILS_TAG_PATTERN.sub("", normalized)

    return normalized


def normalize_markdown(content: str) -> str:
    """
    Normalize source Markdown before section extraction.

    Args:
        content: Raw Markdown content.

    Returns:
        Normalized Markdown content.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = remove_html_comments(normalized)
    normalized = normalize_details_blocks(normalized)

    return normalized.strip()


def fallback_title(source_path: str) -> str:
    """
    Generate a readable title from a source filename.

    Args:
        source_path: Relative Markdown source path.

    Returns:
        Human-readable fallback title.
    """
    stem = Path(source_path).stem
    title = stem.replace("_", " ").replace("-", " ")

    return " ".join(title.split()).strip().title()


def source_category(source_path: str) -> str:
    """
    Determine a document category from its relative source path.

    Args:
        source_path: POSIX-style path relative to the documentation root.

    Returns:
        Top-level directory name or ``base`` for root-level files.
    """
    path = Path(source_path)

    if len(path.parts) == 1:
        return "base"

    return path.parts[0]


def source_access_level(category: str) -> str:
    """
    Determine the default document access level.

    Args:
        category: Document category.

    Returns:
        ``internal`` for internal-only documentation, otherwise ``standard``.
    """
    if category == "internal_only":
        return "internal"

    return "standard"


def is_fence_closing_line(
    line: str,
    fence_character: str,
    fence_length: int,
) -> bool:
    """
    Determine whether a line closes the active Markdown code fence.

    Args:
        line: Current Markdown line.
        fence_character: Backtick or tilde used by the opening fence.
        fence_length: Number of fence characters in the opening fence.

    Returns:
        True when the line closes the active code fence.
    """
    stripped = line.lstrip()

    if not stripped.startswith(fence_character * fence_length):
        return False

    fence_text = stripped.split(maxsplit=1)[0]

    return (
        set(fence_text) == {fence_character}
        and len(fence_text) >= fence_length
    )


def finalize_section(
    pending_section: PendingSection | None,
    sections: list[ParsedSection],
) -> None:
    """
    Add a pending section to the parsed output if it has meaningful content.

    Args:
        pending_section: Section currently being assembled.
        sections: Parsed section destination.
    """
    if pending_section is None:
        return

    content = "\n".join(pending_section.content_lines).strip()

    if not content:
        return

    sections.append(
        ParsedSection(
            heading=pending_section.heading,
            heading_level=pending_section.heading_level,
            heading_path=pending_section.heading_path,
            content=content,
        )
    )


def parse_markdown(
    content: str,
    source_path: str,
) -> ParsedDocument:
    """
    Parse Markdown into heading-aware logical sections.

    Args:
        content: Raw Markdown content.
        source_path: Relative path used to identify the document.

    Returns:
        Parsed document and its content-bearing sections.
    """
    normalized_content = normalize_markdown(content)
    lines = normalized_content.splitlines()

    category = source_category(source_path)
    access_level = source_access_level(category)
    title = fallback_title(source_path)

    heading_stack: list[tuple[int, str]] = []
    sections: list[ParsedSection] = []
    pending_section: PendingSection | None = None

    preamble_lines: list[str] = []

    inside_fence = False
    fence_character = ""
    fence_length = 0

    for line in lines:
        fence_match = FENCE_PATTERN.match(line)

        if fence_match:
            fence_marker = fence_match.group(1)
            marker_character = fence_marker[0]

            if not inside_fence:
                inside_fence = True
                fence_character = marker_character
                fence_length = len(fence_marker)
            elif (
                marker_character == fence_character
                and is_fence_closing_line(
                    line,
                    fence_character,
                    fence_length,
                )
            ):
                inside_fence = False
                fence_character = ""
                fence_length = 0

            if pending_section is not None:
                pending_section.content_lines.append(line)
            else:
                preamble_lines.append(line)

            continue

        heading_match = (
            None
            if inside_fence
            else ATX_HEADING_PATTERN.match(line)
        )

        if heading_match is None:
            if pending_section is not None:
                pending_section.content_lines.append(line)
            else:
                preamble_lines.append(line)

            continue

        finalize_section(pending_section, sections)

        heading_level = len(heading_match.group(1))
        heading = normalize_heading(heading_match.group(2))

        if not heading:
            pending_section = None
            continue

        if heading_level == 1 and title == fallback_title(source_path):
            title = heading

        heading_stack = [
            existing_heading
            for existing_heading in heading_stack
            if existing_heading[0] < heading_level
        ]
        heading_stack.append((heading_level, heading))

        heading_path = tuple(
            heading_text
            for _, heading_text in heading_stack
        )

        pending_section = PendingSection(
            heading=heading,
            heading_level=heading_level,
            heading_path=heading_path,
            content_lines=[],
        )

    finalize_section(pending_section, sections)

    preamble = "\n".join(preamble_lines).strip()

    if preamble:
        sections.insert(
            0,
            ParsedSection(
                heading=title,
                heading_level=0,
                heading_path=(title,),
                content=preamble,
            )
        )

    return ParsedDocument(
        source_path=source_path,
        category=category,
        access_level=access_level,
        title=title,
        sections=tuple(sections),
        raw_content=normalized_content,
    )


def parse_markdown_file(
    path: Path,
    source_path: str,
) -> ParsedDocument:
    """
    Read and parse a Markdown file.

    Args:
        path: Absolute path to the Markdown file.
        source_path: Relative source path stored in the index.

    Returns:
        Parsed Markdown document.
    """
    content = path.read_text(encoding="utf-8")

    return parse_markdown(
        content=content,
        source_path=source_path,
    )

