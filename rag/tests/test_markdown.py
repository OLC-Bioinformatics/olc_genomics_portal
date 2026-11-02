#!/usr/bin/env python3

"""Tests for heading-aware Markdown parsing."""

from pathlib import Path

from ingestion.markdown import (
    fallback_title,
    normalize_markdown,
    parse_markdown,
    parse_markdown_file,
)


def test_parser_extracts_heading_hierarchy() -> None:
    """Sections retain their complete heading hierarchy."""
    content = """
# GeneSeekr

### What does it do?

GeneSeekr detects gene targets in FASTA-formatted files.

### How do I use it?

#### Subject

In the `Subject` field, put `geneseekr`.

#### Description

Include `analysis=resfinder`.
"""

    document = parse_markdown(
        content=content,
        source_path="analysis/geneseekr.md",
    )

    assert document.title == "GeneSeekr"
    assert document.category == "analysis"
    assert document.access_level == "standard"
    assert document.section_count == 3

    assert document.sections[0].heading_path == (
        "GeneSeekr",
        "What does it do?",
    )
    assert (
        "GeneSeekr detects gene targets"
        in document.sections[0].content
    )

    assert document.sections[1].heading_path == (
        "GeneSeekr",
        "How do I use it?",
        "Subject",
    )
    assert "`geneseekr`" in document.sections[1].content

    assert document.sections[2].heading_path == (
        "GeneSeekr",
        "How do I use it?",
        "Description",
    )
    assert "`analysis=resfinder`" in document.sections[2].content


def test_parser_removes_html_comments() -> None:
    """Instructions in HTML comments are not retained."""
    content = """
# MobSuite

### What can go wrong?

Current troubleshooting guidance.

<!--
Old FTP instructions should not be indexed.
This comment spans multiple lines.
-->

Current Dropbox guidance.
"""

    document = parse_markdown(
        content=content,
        source_path="analysis/mobsuite.md",
    )

    assert document.section_count == 1

    section_content = document.sections[0].content

    assert "Current troubleshooting guidance." in section_content
    assert "Current Dropbox guidance." in section_content
    assert "Old FTP instructions" not in section_content
    assert "<!--" not in document.raw_content
    assert "-->" not in document.raw_content


def test_parser_retains_details_content() -> None:
    """Visible content inside details elements is retained."""
    content = """
# OLC Redmine Automator

<details>
  <summary><b>Detect and type plasmids</b></summary>

Use MobSuite to detect plasmids.

</details>
"""

    document = parse_markdown(
        content=content,
        source_path="index.md",
    )

    assert document.section_count == 1

    section_content = document.sections[0].content

    assert "**Detect and type plasmids**" in section_content
    assert "Use MobSuite to detect plasmids." in section_content
    assert "<details>" not in document.raw_content
    assert "</details>" not in document.raw_content
    assert "<summary>" not in document.raw_content
    assert "</summary>" not in document.raw_content


def test_hashes_inside_code_fences_are_not_headings() -> None:
    """Heading-like lines inside code fences remain section content."""
    content = """
# Example Tool

### Example

```text
# This is part of the example
analysis=resfinder
```

The example continues here.
"""

    document = parse_markdown(
        content=content,
        source_path="analysis/example_tool.md",
    )

    assert document.section_count == 1

    section = document.sections[0]

    assert section.heading_path == (
        "Example Tool",
        "Example",
    )
    assert "# This is part of the example" in section.content
    assert "analysis=resfinder" in section.content
    assert "The example continues here." in section.content


def test_parser_preserves_lists_and_inline_code() -> None:
    """Markdown lists and inline code are preserved."""
    content = """
# GeneSeekr

#### Optional Components

- BLAST program:
    - `blastn`
    - `blastp`
- Minimum cutoff:
    - `cutoff=80`
"""

    document = parse_markdown(
        content=content,
        source_path="analysis/geneseekr.md",
    )

    assert document.section_count == 1

    section_content = document.sections[0].content

    assert "- BLAST program:" in section_content
    assert "    - `blastn`" in section_content
    assert "    - `blastp`" in section_content
    assert "- Minimum cutoff:" in section_content
    assert "    - `cutoff=80`" in section_content


def test_parser_uses_filename_when_h1_is_missing() -> None:
    """The filename provides a title when the document has no H1."""
    content = """
### Subject

Use `external_retrieve`.
"""

    document = parse_markdown(
        content=content,
        source_path="data/external_retrieve.md",
    )

    assert document.title == "External Retrieve"
    assert document.section_count == 1
    assert document.sections[0].heading_path == ("Subject",)
    assert "`external_retrieve`" in document.sections[0].content


def test_parser_includes_content_before_first_heading() -> None:
    """Content preceding the first heading is retained as a preamble."""
    content = """
This document begins with introductory content.

# Tool Name

### Usage

Use the tool carefully.
"""

    document = parse_markdown(
        content=content,
        source_path="analysis/tool_name.md",
    )

    assert document.title == "Tool Name"
    assert document.section_count == 2

    preamble = document.sections[0]

    assert preamble.heading_level == 0
    assert preamble.heading_path == ("Tool Name",)
    assert "introductory content" in preamble.content

    usage_section = document.sections[1]

    assert usage_section.heading_path == (
        "Tool Name",
        "Usage",
    )
    assert "Use the tool carefully." in usage_section.content


def test_internal_only_documents_are_classified() -> None:
    """Documents under internal_only receive internal access metadata."""
    content = """
# Merge

### How do I use it?

Internal instructions.
"""

    document = parse_markdown(
        content=content,
        source_path="internal_only/merge.md",
    )

    assert document.category == "internal_only"
    assert document.access_level == "internal"
    assert document.title == "Merge"


def test_fallback_title_normalizes_filename() -> None:
    """Fallback titles normalize underscores and hyphens."""
    assert (
        fallback_title("analysis/gfa_retrieve.md")
        == "Gfa Retrieve"
    )
    assert (
        fallback_title("analysis/unknown-isolate.md")
        == "Unknown Isolate"
    )


def test_parse_markdown_file_reads_utf8_document(
    tmp_path: Path,
) -> None:
    """Markdown files are read using UTF-8."""
    markdown_path = tmp_path / "mobsuite.md"

    markdown_path.write_text(
        (
            "# MobSuite\n"
            "\n"
            "### Description\n"
            "\n"
            "Detects plasmids in draft assemblies.\n"
        ),
        encoding="utf-8",
    )

    document = parse_markdown_file(
        path=markdown_path,
        source_path="analysis/mobsuite.md",
    )

    assert document.title == "MobSuite"
    assert document.section_count == 1
    assert "Detects plasmids" in document.sections[0].content


def test_normalize_markdown_handles_line_endings() -> None:
    """Windows and classic Mac line endings are normalized."""
    content = (
        "# Tool\r\n"
        "\r\n"
        "### Description\r\n"
        "\r\n"
        "Some content.\r"
    )

    normalized = normalize_markdown(content)

    assert "\r" not in normalized
    assert "Some content." in normalized

