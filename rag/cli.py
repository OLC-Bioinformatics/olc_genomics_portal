#!/usr/bin/env python3

"""Administrative CLI for the RedmineAssistant RAG service."""

# Standard library imports
from collections import Counter
from pathlib import Path
import argparse
import logging
import statistics
import sys
import time

# Third-party imports
import psycopg

# Local application imports
from database import (
    check_database_connection,
    get_database_status,
    run_migrations,
)
from evaluation import (
    DEFAULT_EVALUATION_PATH,
    EvaluationError,
    evaluate_questions,
    load_evaluation_questions,
    print_evaluation_summary,
    write_json_report,
)
from ingestion.chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_TARGET_CHARS,
    DocumentChunk,
    chunk_document,
)
from ingestion.discovery import (
    discover_markdown_documents,
    discovery_summary,
    documentation_root,
)
from ingestion.indexer import index_documentation
from ingestion.markdown import parse_markdown_file
from retrieval import (
    RetrievalError,
    retrieve_chunks,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

LOGGER = logging.getLogger("redmine-assistant-cli")


def wait_for_database(timeout_seconds: int, interval_seconds: int) -> int:
    """
    Wait for PostgreSQL to accept connections.

    Args:
        timeout_seconds: Maximum number of seconds to wait.
        interval_seconds: Delay between connection attempts.

    Returns:
        Shell-compatible status code.
    """
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            if check_database_connection():
                LOGGER.info("Database is available")
                return 0
        except psycopg.Error as exc:
            LOGGER.info("Database is not ready: %s", exc)

        time.sleep(interval_seconds)

    LOGGER.error(
        "Database did not become available within %s seconds",
        timeout_seconds,
    )
    return 1


def migrate_database() -> int:
    """Apply pending database migrations."""
    migrations = run_migrations()

    if migrations:
        for migration in migrations:
            LOGGER.info("Applied migration: %s", migration)
    else:
        LOGGER.info("No pending database migrations")

    return 0


def print_status() -> int:
    """Print database/index status."""
    status = get_database_status()

    print(f"Migrations: {status['migration_count']}")
    print(f"Documents: {status['documents']}")
    print(f"Chunks: {status['chunks']}")

    return 0


def discover_documents(show_paths: bool = False) -> int:
    """
    Discover and summarize indexable Markdown documents.

    Args:
        show_paths: Print every included source path when True.

    Returns:
        Shell-compatible status code.
    """
    root = documentation_root()
    documents = discover_markdown_documents(root)
    summary = discovery_summary(documents)

    print(f"Documentation root: {root}")
    print(f"Markdown documents discovered: {summary['document_count']}")

    print("Excluded directories:")
    for excluded_directory in summary["excluded_directories"]:
        print(f"- {excluded_directory}")

    print("Documents by category:")
    for category, count in summary["categories"].items():
        print(f"- {category}: {count}")

    if show_paths:
        print("Documents:")
        for document in documents:
            print(f"- {document.source_path}")

    return 0


def dry_run_index(
    source_path: str | None = None,
    show_content: bool = False,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> int:
    """
    Parse and chunk documentation without writing to PostgreSQL.

    Args:
        source_path: Optional relative source path to inspect.
        show_content: Display full chunk content when True.
        target_chars: Preferred chunk size.
        max_chars: Maximum preferred chunk size.

    Returns:
        Shell-compatible status code.
    """
    root = documentation_root()
    discovered_documents = discover_markdown_documents(root)

    if source_path is not None:
        discovered_documents = [
            document
            for document in discovered_documents
            if document.source_path == source_path
        ]

        if not discovered_documents:
            LOGGER.error(
                "Requested source was not discovered: %s",
                source_path,
            )
            return 1

    all_chunks: list[DocumentChunk] = []
    document_failures: list[tuple[str, str]] = []
    section_count = 0
    split_section_count = 0

    for discovered_document in discovered_documents:
        try:
            parsed_document = parse_markdown_file(
                path=discovered_document.absolute_path,
                source_path=discovered_document.source_path,
            )

            document_chunks = chunk_document(
                document=parsed_document,
                target_chars=target_chars,
                max_chars=max_chars,
            )

            section_count += parsed_document.section_count

            split_section_count += len(
                {
                    chunk.section_index
                    for chunk in document_chunks
                    if chunk.split_section
                }
            )

            all_chunks.extend(document_chunks)

            if show_content:
                print("=" * 80)
                print(f"Source: {parsed_document.source_path}")
                print(f"Title: {parsed_document.title}")
                print(f"Sections: {parsed_document.section_count}")
                print(f"Chunks: {len(document_chunks)}")
                print()

                for chunk in document_chunks:
                    print(f"Chunk key: {chunk.chunk_key}")
                    print(f"Section: {chunk.heading_path_text}")
                    print(f"Characters: {chunk.character_count}")
                    print(f"Access level: {chunk.access_level}")
                    print(f"Split section: {chunk.split_section}")
                    print()
                    print(chunk.content)
                    print("-" * 80)

        except Exception as exc:
            LOGGER.exception(
                "Failed to parse or chunk %s",
                discovered_document.source_path,
            )
            document_failures.append(
                (
                    discovered_document.source_path,
                    str(exc),
                )
            )

    access_counts = Counter(chunk.access_level for chunk in all_chunks)

    character_counts = [chunk.character_count for chunk in all_chunks]

    oversized_chunks = [
        chunk for chunk in all_chunks if chunk.character_count > max_chars
    ]

    print()
    print("Dry-run indexing summary")
    print("========================")
    print(f"Documentation root: {root}")
    print(f"Documents selected: {len(discovered_documents)}")
    print(f"Documents parsed: {len(discovered_documents) - len(document_failures)}")
    print(f"Documents failed: {len(document_failures)}")
    print(f"Sections found: {section_count}")
    print(f"Chunks generated: {len(all_chunks)}")
    print(f"Sections split: {split_section_count}")
    print(f"Target characters: {target_chars}")
    print(f"Maximum preferred characters: {max_chars}")

    print()
    print("Access levels:")

    for access_level, count in sorted(access_counts.items()):
        print(f"- {access_level}: {count}")

    if character_counts:
        print()
        print("Chunk sizes:")
        print(f"- minimum: {min(character_counts)} characters")
        print(f"- median: {int(statistics.median(character_counts))} characters")
        print(f"- maximum: {max(character_counts)} characters")

    largest_chunks = sorted(
        all_chunks,
        key=lambda chunk: chunk.character_count,
        reverse=True,
    )[:10]

    if largest_chunks:
        print()
        print("Largest chunks:")

        for number, chunk in enumerate(
            largest_chunks,
            start=1,
        ):
            print(f"{number}. {chunk.source_path} — {chunk.heading_path_text}")
            print(f"   {chunk.character_count} characters")

    if oversized_chunks:
        print()
        print("Oversized chunks (usually intact fenced code blocks):")

        for chunk in oversized_chunks:
            print(
                f"- {chunk.source_path} — "
                f"{chunk.heading_path_text}: "
                f"{chunk.character_count} characters"
            )

    if document_failures:
        print()
        print("Failures:")

        for failed_source, error_message in document_failures:
            print(f"- {failed_source}: {error_message}")

        return 1

    return 0


def index_documents() -> int:
    """Index changed Markdown documents into PostgreSQL."""
    summary = index_documentation()

    print("Documentation indexing summary")
    print("==============================")
    print(f"Documents discovered: {summary.discovered}")
    print(f"Documents added: {summary.added}")
    print(f"Documents updated: {summary.updated}")
    print(f"Documents unchanged: {summary.unchanged}")
    print(f"Documents removed: {summary.removed}")
    print(f"Chunks embedded: {summary.chunks_embedded}")
    print(f"Failures: {summary.failure_count}")

    if summary.failures:
        print()
        print("Failed documents:")

        for source_path, error_message in summary.failures:
            print(f"- {source_path}: {error_message}")

        return 1

    return 0


def search_documents(
    query: str,
    limit: int | None = None,
    include_internal: bool = False,
    show_content: bool = False,
) -> int:
    """
    Search indexed documentation.

    Args:
        query: Semantic search query.
        limit: Maximum number of results.
        include_internal: Include internal-only documentation.
        show_content: Print complete chunk content instead of a preview.

    Returns:
        Shell-compatible status code.
    """
    results = retrieve_chunks(
        query=query,
        limit=limit,
        include_internal=include_internal,
    )

    print(f"Query: {query.strip()}")
    print(f"Results: {len(results)}")
    print()

    if not results:
        print("No indexed documentation results were found.")
        return 0

    for result in results:
        print(f"{result.rank}. {result.document_title} ({result.score:.4f})")
        print(f"   Source: {result.source_path}")
        print(f"   Section: {result.heading_path}")
        print(f"   Access level: {result.access_level}")
        print(f"   Chunk key: {result.chunk_key}")
        print()

        if show_content:
            displayed_content = result.content
        else:
            displayed_content = " ".join(result.content.split())

            if len(displayed_content) > 350:
                displayed_content = displayed_content[:347].rstrip() + "..."

        print(displayed_content)
        print("-" * 80)

    return 0


def evaluate_retrieval(
    questions_path: str,
    top_k: int,
    category: str | None = None,
    output_json: str | None = None,
    show_failures: bool = True,
) -> int:
    """
    Evaluate semantic retrieval against a YAML question set.

    Args:
        questions_path: Evaluation YAML path.
        top_k: Number of results retrieved per question.
        category: Optional question-category filter.
        output_json: Optional JSON report destination.
        show_failures: Print failed Hit@5 questions.

    Returns:
        Shell-compatible status code.
    """
    evaluation_path = Path(questions_path)

    questions = load_evaluation_questions(
        evaluation_path
    )

    summary = evaluate_questions(
        questions=questions,
        evaluation_path=evaluation_path,
        top_k=top_k,
        category=category,
    )

    print_evaluation_summary(
        summary=summary,
        show_failures=show_failures,
    )

    if output_json is not None:
        report_path = Path(output_json)

        write_json_report(
            summary=summary,
            path=report_path,
        )

        print()
        print(f"JSON report written to: {report_path}")

    if summary.errors:
        return 1

    if summary.internal_leakage_count:
        return 1

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="RedmineAssistant RAG administrative commands"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    wait_parser = subparsers.add_parser(
        "wait-for-db",
        help="Wait for PostgreSQL to become available",
    )
    wait_parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Maximum seconds to wait",
    )
    wait_parser.add_argument(
        "--interval",
        type=int,
        default=2,
        help="Seconds between connection attempts",
    )

    subparsers.add_parser(
        "migrate",
        help="Apply pending database migrations",
    )

    subparsers.add_parser(
        "status",
        help="Print database and index status",
    )

    discover_parser = subparsers.add_parser(
        "discover",
        help="Discover Markdown documents eligible for indexing",
    )
    discover_parser.add_argument(
        "--show-paths",
        action="store_true",
        help="Print every included relative source path",
    )
    dry_run_parser = subparsers.add_parser(
        "dry-run-index",
        help=(
            "Parse and chunk documentation without writing "
            "to PostgreSQL"
        ),
    )
    dry_run_parser.add_argument(
        "--source",
        help=(
            "Process only the specified relative source path, "
            "for example analysis/geneseekr.md"
        ),
    )
    dry_run_parser.add_argument(
        "--show-content",
        action="store_true",
        help="Print the complete content of every generated chunk",
    )
    dry_run_parser.add_argument(
        "--target-chars",
        type=int,
        default=DEFAULT_TARGET_CHARS,
        help="Preferred chunk size in characters",
    )
    dry_run_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="Maximum preferred chunk size in characters",
    )
    subparsers.add_parser(
        "index",
        help="Index changed documentation into PostgreSQL",
    )
    search_parser = subparsers.add_parser(
        "search",
        help="Search indexed documentation semantically",
    )
    search_parser.add_argument(
        "query",
        help="Question or search phrase",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Maximum number of results; defaults to RAG_TOP_K"
        ),
    )
    search_parser.add_argument(
        "--include-internal",
        action="store_true",
        help="Include internal-only documentation",
    )
    search_parser.add_argument(
        "--show-content",
        action="store_true",
        help="Print complete chunk content",
    )
    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Evaluate semantic retrieval using YAML questions",
    )
    evaluate_parser.add_argument(
        "--questions",
        default=str(DEFAULT_EVALUATION_PATH),
        help="Path to evaluation_questions.yaml",
    )
    evaluate_parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of results retrieved per question",
    )
    evaluate_parser.add_argument(
        "--category",
        help="Evaluate only the specified question category",
    )
    evaluate_parser.add_argument(
        "--output-json",
        help="Write the complete evaluation report to JSON",
    )
    evaluate_parser.add_argument(
        "--no-show-failures",
        action="store_true",
        help="Do not print failed Hit@5 question details",
    )
    return parser


def main() -> int:
    """Run the selected administrative command."""
    parser = build_parser()
    arguments = parser.parse_args()

    try:
        if arguments.command == "wait-for-db":
            return wait_for_database(
                timeout_seconds=arguments.timeout,
                interval_seconds=arguments.interval,
            )

        if arguments.command == "migrate":
            return migrate_database()

        if arguments.command == "status":
            return print_status()

        if arguments.command == "discover":
            return discover_documents(
                show_paths=arguments.show_paths,
            )
        if arguments.command == "dry-run-index":
            return dry_run_index(
                source_path=arguments.source,
                show_content=arguments.show_content,
                target_chars=arguments.target_chars,
                max_chars=arguments.max_chars,
            )
        if arguments.command == "index":
            return index_documents()
        if arguments.command == "search":
            return search_documents(
                query=arguments.query,
                limit=arguments.limit,
                include_internal=arguments.include_internal,
                show_content=arguments.show_content,
            )
        if arguments.command == "evaluate":
            return evaluate_retrieval(
                questions_path=arguments.questions,
                top_k=arguments.top_k,
                category=arguments.category,
                output_json=arguments.output_json,
                show_failures=not arguments.no_show_failures,
            )
        parser.error(f"Unknown command: {arguments.command}")
        return 2

    except psycopg.Error as exc:
        LOGGER.error("Database operation failed: %s", exc)
        return 1
    except (FileNotFoundError, NotADirectoryError) as exc:
        LOGGER.error("Documentation discovery failed: %s", exc)
        return 1
    except RetrievalError as exc:
        LOGGER.error("Documentation search failed: %s", exc)
        return 1
    except EvaluationError as exc:
        LOGGER.error("Retrieval evaluation failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
