#!/usr/bin/env python3

"""Administrative CLI for the RedmineAssistant RAG service."""

import argparse
import logging
import sys
import time

import psycopg

from database import (
    check_database_connection,
    get_database_status,
    run_migrations,
)

from ingestion.discovery import (
    discover_markdown_documents,
    discovery_summary,
    documentation_root,
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

        parser.error(f"Unknown command: {arguments.command}")
        return 2

    except psycopg.Error as exc:
        LOGGER.error("Database operation failed: %s", exc)
        return 1
    except (FileNotFoundError, NotADirectoryError) as exc:
        LOGGER.error("Documentation discovery failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
