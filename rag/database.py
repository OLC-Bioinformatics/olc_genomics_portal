#!/usr/bin/env python3

"""PostgreSQL operations for the RedmineAssistant RAG service."""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from config import settings


MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent / "migrations"


def create_connection() -> Connection:
    """
    Create a PostgreSQL database connection.

    Returns:
        An open psycopg connection.
    """
    return psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        row_factory=dict_row,
        connect_timeout=5,
    )


@contextmanager
def database_connection() -> Generator[Connection, None, None]:
    """
    Provide a database connection as a context manager.

    The connection commits when the context exits successfully and rolls
    back if an exception escapes the context.
    """
    with create_connection() as connection:
        yield connection


def check_database_connection() -> bool:
    """
    Confirm that PostgreSQL is reachable.

    Returns:
        True when a simple query succeeds.
    """
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 AS database_check")
            result = cursor.fetchone()

    return (
        result is not None
        and result["database_check"] == 1
    )


def ensure_migration_table(connection: Connection) -> None:
    """
    Create the migration ledger if it does not already exist.

    Args:
        connection: Active PostgreSQL connection.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def applied_migrations(connection: Connection) -> set:
    """
    Return the set of previously applied migration versions.

    Args:
        connection: Active PostgreSQL connection.

    Returns:
        Applied migration filenames.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT version FROM schema_migrations")
        rows = cursor.fetchall()

    return {row["version"] for row in rows}


def run_migrations() -> list:
    """
    Apply all pending SQL migrations.

    Returns:
        Migration filenames applied by this invocation.

    Raises:
        FileNotFoundError: If the migrations directory does not exist.
        RuntimeError: If no migration files are available.
        psycopg.Error: If a database operation fails.
    """
    if not MIGRATIONS_DIRECTORY.is_dir():
        raise FileNotFoundError(
            f"Migrations directory does not exist: "
            f"{MIGRATIONS_DIRECTORY}"
        )

    migration_files = sorted(MIGRATIONS_DIRECTORY.glob("*.sql"))

    if not migration_files:
        raise RuntimeError(
            f"No SQL migrations found in {MIGRATIONS_DIRECTORY}"
        )

    applied_now: list[str] = []

    with database_connection() as connection:
        ensure_migration_table(connection)
        already_applied = applied_migrations(connection)

        for migration_file in migration_files:
            version = migration_file.name

            if version in already_applied:
                continue

            migration_sql = migration_file.read_text(encoding="utf-8")

            with connection.cursor() as cursor:
                cursor.execute(migration_sql)
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (version)
                    VALUES (%s)
                    """,
                    (version,),
                )

            connection.commit()
            applied_now.append(version)

    return applied_now


def table_exists(
    connection: Connection,
    table_name: str,
) -> bool:
    """
    Check whether a table exists in the public schema.

    Args:
        connection: Active PostgreSQL connection.
        table_name: Unqualified table name.

    Returns:
        True if the table exists.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = %s
            ) AS table_exists
            """,
            (table_name,),
        )
        result = cursor.fetchone()

    return bool(result and result["table_exists"])


def count_table_rows(
    connection: Connection,
    table_name: str,
) -> int:
    """
    Count rows in an approved application table.

    Args:
        connection: Active PostgreSQL connection.
        table_name: Approved table name.

    Returns:
        Number of rows in the table.

    Raises:
        ValueError: If the table name is not approved.
    """
    allowed_tables = {
        "documents",
        "document_chunks",
        "schema_migrations",
    }

    if table_name not in allowed_tables:
        raise ValueError(
            f"Row counting is not allowed for table: {table_name}"
        )

    # The table name cannot be supplied as a standard SQL value parameter.
    # It is safe to interpolate here because it has been checked against the
    # fixed allow-list above.
    query = f"SELECT COUNT(*) AS row_count FROM {table_name}"

    with connection.cursor() as cursor:
        cursor.execute(query)
        result = cursor.fetchone()

    return int(result["row_count"])


def get_database_status() -> dict[str, int]:
    """
    Return non-sensitive database and index status information.

    Returns:
        Schema migration, document, and chunk counts.
    """
    with database_connection() as connection:
        ensure_migration_table(connection)

        migration_count = count_table_rows(
            connection,
            "schema_migrations",
        )

        document_count = 0
        chunk_count = 0

        if table_exists(connection, "documents"):
            document_count = count_table_rows(
                connection,
                "documents",
            )

        if table_exists(connection, "document_chunks"):
            chunk_count = count_table_rows(
                connection,
                "document_chunks",
            )

    return {
        "migration_count": migration_count,
        "documents": document_count,
        "chunks": chunk_count,
    }
