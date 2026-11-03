#!/usr/bin/env python3

"""Generate a Markdown and CSV inventory of Redmine documentation pages."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

STANDARD_SECTIONS = (
    "What does it do?",
    "How do I use it?",
    "Subject",
    "Description",
    "Attachments",
    "Optional parameters",
    "Examples",
    "Interpreting results",
    "How long does it take?",
    "What can go wrong?",
    "Related automators",
)

STATUS_DEFAULT = "not_started"


@dataclass(frozen=True)
class PageInventory:
    source_path: str
    title: str
    category: str
    access_level: str
    line_count: int
    character_count: int
    headings: tuple[str, ...]
    missing_sections: tuple[str, ...]
    local_link_count: int
    broken_local_links: tuple[str, ...]
    malformed_heading_count: int
    status: str = STATUS_DEFAULT
    owner: str = ""
    technical_reviewer: str = ""
    notes: str = ""


def normalize_heading(value: str) -> str:
    """Normalize heading text for case-insensitive comparisons."""
    return re.sub(r"\s+", " ", value.strip()).casefold()


def page_category(relative_path: Path) -> str:
    """Return the first path component or 'root'."""
    if len(relative_path.parts) <= 1:
        return "root"
    return relative_path.parts[0]


def page_access_level(relative_path: Path) -> str:
    """Determine the documentation access level from its path."""
    if relative_path.parts and relative_path.parts[0] == "internal_only":
        return "internal"
    return "standard"


def extract_title(headings: list[tuple[int, str]], path: Path) -> str:
    """Use the first H1, then first heading, then filename as the title."""
    for level, text in headings:
        if level == 1:
            return text
    if headings:
        return headings[0][1]
    return path.stem.replace("_", " ").replace("-", " ").title()


def resolve_local_link(
    documentation_root: Path,
    source_path: Path,
    destination: str,
) -> tuple[bool, str | None]:
    """Return whether a Markdown destination is local and its normalized path."""
    destination = destination.strip()

    if not destination or destination.startswith(("#", "/", "//")):
        return False, None

    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", destination):
        return False, None

    path_without_fragment = destination.split("#", 1)[0].split("?", 1)[0]
    if not path_without_fragment:
        return False, None

    resolved = (source_path.parent / path_without_fragment).resolve()
    root = documentation_root.resolve()

    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return True, path_without_fragment

    return True, relative.as_posix()


def inspect_page(documentation_root: Path, path: Path) -> PageInventory:
    """Inspect one Markdown page."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    relative_path = path.relative_to(documentation_root)

    headings: list[tuple[int, str]] = []
    malformed_heading_count = 0
    in_fence = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        match = HEADING_PATTERN.match(line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip()))
        elif re.match(r"^#{1,6}[^#\s]", line):
            malformed_heading_count += 1

    normalized_headings = {
        normalize_heading(text_value)
        for _, text_value in headings
    }
    missing_sections = tuple(
        section
        for section in STANDARD_SECTIONS
        if normalize_heading(section) not in normalized_headings
    )

    local_link_count = 0
    broken_local_links: list[str] = []

    for destination in LINK_PATTERN.findall(text):
        is_local, normalized_destination = resolve_local_link(
            documentation_root,
            path,
            destination,
        )
        if not is_local:
            continue
        local_link_count += 1
        if normalized_destination is None:
            continue
        target = documentation_root / normalized_destination
        if not target.exists():
            broken_local_links.append(destination)

    return PageInventory(
        source_path=relative_path.as_posix(),
        title=extract_title(headings, path),
        category=page_category(relative_path),
        access_level=page_access_level(relative_path),
        line_count=len(lines),
        character_count=len(text),
        headings=tuple(text_value for _, text_value in headings),
        missing_sections=missing_sections,
        local_link_count=local_link_count,
        broken_local_links=tuple(sorted(set(broken_local_links))),
        malformed_heading_count=malformed_heading_count,
    )


def discover_pages(documentation_root: Path) -> list[Path]:
    """Discover Markdown pages in stable order."""
    return sorted(
        path
        for path in documentation_root.rglob("*.md")
        if path.is_file()
    )


def write_csv(path: Path, pages: list[PageInventory]) -> None:
    """Write a spreadsheet-friendly CSV inventory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "source_path",
        "title",
        "category",
        "access_level",
        "status",
        "owner",
        "technical_reviewer",
        "line_count",
        "character_count",
        "heading_count",
        "missing_section_count",
        "missing_sections",
        "local_link_count",
        "broken_local_link_count",
        "broken_local_links",
        "malformed_heading_count",
        "notes",
    )

    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        for page in pages:
            writer.writerow(
                {
                    "source_path": page.source_path,
                    "title": page.title,
                    "category": page.category,
                    "access_level": page.access_level,
                    "status": page.status,
                    "owner": page.owner,
                    "technical_reviewer": page.technical_reviewer,
                    "line_count": page.line_count,
                    "character_count": page.character_count,
                    "heading_count": len(page.headings),
                    "missing_section_count": len(page.missing_sections),
                    "missing_sections": " | ".join(page.missing_sections),
                    "local_link_count": page.local_link_count,
                    "broken_local_link_count": len(page.broken_local_links),
                    "broken_local_links": " | ".join(page.broken_local_links),
                    "malformed_heading_count": page.malformed_heading_count,
                    "notes": page.notes,
                }
            )


def markdown_escape(value: str) -> str:
    """Escape table-sensitive Markdown characters."""
    return value.replace("|", "\\|").replace("\n", " ")


def write_markdown(
    path: Path,
    pages: list[PageInventory],
    documentation_root: Path,
) -> None:
    """Write a reviewer-friendly Markdown inventory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    standard_count = sum(page.access_level == "standard" for page in pages)
    internal_count = sum(page.access_level == "internal" for page in pages)
    broken_count = sum(len(page.broken_local_links) for page in pages)
    malformed_count = sum(page.malformed_heading_count for page in pages)

    lines = [
        "# Documentation Rewrite Inventory",
        "",
        f"Generated at: `{generated_at}`",
        "",
        f"Documentation root: `{documentation_root}`",
        "",
        "## Summary",
        "",
        f"- Total Markdown pages: {len(pages)}",
        f"- Standard-access pages: {standard_count}",
        f"- Internal-access pages: {internal_count}",
        f"- Broken local links: {broken_count}",
        f"- Malformed headings: {malformed_count}",
        "",
        "## Rewrite Tracking",
        "",
        "| Source path | Title | Category | Access | Status | Missing standard sections | Broken links | Reviewer |",
        "|---|---|---|---|---|---:|---:|---|",
    ]

    for page in pages:
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{markdown_escape(page.source_path)}`",
                    markdown_escape(page.title),
                    markdown_escape(page.category),
                    page.access_level,
                    page.status,
                    str(len(page.missing_sections)),
                    str(len(page.broken_local_links)),
                    "",
                )
            )
            + " |"
        )

    lines.extend(["", "## Detailed Findings", ""])

    for page in pages:
        lines.extend(
            [
                f"### `{page.source_path}`",
                "",
                f"- **Title:** {page.title}",
                f"- **Category:** `{page.category}`",
                f"- **Access level:** `{page.access_level}`",
                f"- **Status:** `{page.status}`",
                f"- **Lines:** {page.line_count}",
                f"- **Characters:** {page.character_count}",
                f"- **Malformed headings:** {page.malformed_heading_count}",
                "- **Headings:** "
                + (", ".join(f"`{heading}`" for heading in page.headings) or "None"),
                "- **Missing standard sections:** "
                + (", ".join(f"`{section}`" for section in page.missing_sections) or "None"),
                "- **Broken local links:** "
                + (", ".join(f"`{link}`" for link in page.broken_local_links) or "None"),
                "- **Owner:**",
                "- **Technical reviewer:**",
                "- **Notes:**",
                "",
            ]
        )

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a rewrite inventory for Markdown documentation."
    )
    parser.add_argument(
        "--documentation-root",
        type=Path,
        required=True,
        help="Root directory containing Markdown documentation.",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("docs/documentation-rewrite-inventory.md"),
        help="Markdown inventory destination.",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=Path("docs/documentation-rewrite-inventory.csv"),
        help="CSV inventory destination.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    documentation_root = args.documentation_root.resolve()

    if not documentation_root.is_dir():
        print(
            f"Documentation root is not a directory: {documentation_root}",
            file=sys.stderr,
        )
        return 2

    paths = discover_pages(documentation_root)
    if not paths:
        print(
            f"No Markdown files found under: {documentation_root}",
            file=sys.stderr,
        )
        return 2

    pages = [inspect_page(documentation_root, path) for path in paths]
    write_markdown(args.markdown_output, pages, documentation_root)
    write_csv(args.csv_output, pages)

    print(f"Pages inventoried: {len(pages)}")
    print(f"Markdown inventory: {args.markdown_output}")
    print(f"CSV inventory: {args.csv_output}")
    print(
        "Broken local links: "
        f"{sum(len(page.broken_local_links) for page in pages)}"
    )
    print(
        "Malformed headings: "
        f"{sum(page.malformed_heading_count for page in pages)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
