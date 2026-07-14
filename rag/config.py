#!/usr/bin/env python3

"""Configuration for the RedmineAssistant RAG service."""

from dataclasses import dataclass
import os


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is missing."""


def required_environment_variable(name: str) -> str:
    """
    Return a required environment variable.

    Args:
        name: Name of the required environment variable.

    Returns:
        The configured value.

    Raises:
        ConfigurationError: If the variable is missing or empty.
    """
    value = os.getenv(name)

    if value is None or not value.strip():
        raise ConfigurationError(
            f"Required environment variable is not configured: {name}"
        )

    return value


def integer_environment_variable(name: str, default: int) -> int:
    """
    Return an environment variable as an integer.

    Args:
        name: Environment variable name.
        default: Default value when the variable is not configured.

    Returns:
        The configured integer value.

    Raises:
        ConfigurationError: If the value cannot be converted to an integer.
    """
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(
            f"Environment variable {name} must be an integer"
        ) from exc


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    documentation_root: str
    top_k: int
    max_top_k: int

    @classmethod
    def from_environment(cls) -> "Settings":
        """Create settings from the current process environment."""
        return cls(
            db_host=os.getenv("RAG_DB_HOST", "rag-db"),
            db_port=integer_environment_variable("RAG_DB_PORT", 5432),
            db_name=os.getenv("RAG_DB_NAME", "redmine_assistant"),
            db_user=os.getenv("RAG_DB_USER", "redmine_assistant"),
            db_password=required_environment_variable("RAG_DB_PASSWORD"),
            documentation_root=os.getenv(
                "DOCUMENTATION_ROOT",
                "/documentation"
            ),
            top_k=integer_environment_variable("RAG_TOP_K", 5),
            max_top_k=integer_environment_variable("RAG_MAX_TOP_K", 10),
        )


settings = Settings.from_environment()

