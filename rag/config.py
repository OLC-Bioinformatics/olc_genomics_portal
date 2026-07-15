#!/usr/bin/env python3

"""Configuration for the RedmineAssistant RAG service."""

from dataclasses import dataclass
import os


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBEDDING_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
DEFAULT_EMBEDDING_DIMENSION = 384


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is invalid."""


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

    return value.strip()


def integer_environment_variable(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    """
    Return an environment variable as an integer.

    Args:
        name: Environment variable name.
        default: Default value when the variable is not configured.
        minimum: Optional minimum accepted value.

    Returns:
        The configured integer value.

    Raises:
        ConfigurationError: If the value is not an integer or is smaller
            than the configured minimum.
    """
    raw_value = os.getenv(name)

    if raw_value is None or not raw_value.strip():
        value = default
    else:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ConfigurationError(
                f"Environment variable {name} must be an integer"
            ) from exc

    if minimum is not None and value < minimum:
        raise ConfigurationError(
            f"Environment variable {name} must be at least {minimum}"
        )

    return value


def boolean_environment_variable(
    name: str,
    default: bool,
) -> bool:
    """
    Return an environment variable as a Boolean.

    Accepted true values are:

    - 1
    - true
    - yes
    - on

    Accepted false values are:

    - 0
    - false
    - no
    - off

    Matching is case-insensitive.

    Args:
        name: Environment variable name.
        default: Default value when the variable is not configured.

    Returns:
        The configured Boolean value.

    Raises:
        ConfigurationError: If the configured value is not recognized.
    """
    raw_value = os.getenv(name)

    if raw_value is None or not raw_value.strip():
        return default

    normalized = raw_value.strip().casefold()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ConfigurationError(
        f"Environment variable {name} must be one of: "
        "1, true, yes, on, 0, false, no, off"
    )


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    # PostgreSQL settings
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str

    # Documentation and retrieval settings
    documentation_root: str
    top_k: int
    max_top_k: int

    # Embedding model settings
    embedding_model: str
    embedding_model_revision: str
    embedding_dimension: int
    embedding_device: str
    embedding_batch_size: int
    embedding_normalize: bool
    model_cache_dir: str

    @classmethod
    def from_environment(cls) -> "Settings":
        """
        Create settings from the current process environment.

        Returns:
            Validated application settings.

        Raises:
            ConfigurationError: If any configuration value is invalid.
        """
        configured_top_k = integer_environment_variable(
            "RAG_TOP_K",
            5,
            minimum=1,
        )
        configured_max_top_k = integer_environment_variable(
            "RAG_MAX_TOP_K",
            10,
            minimum=1,
        )

        if configured_top_k > configured_max_top_k:
            raise ConfigurationError("RAG_TOP_K cannot be greater than RAG_MAX_TOP_K")

        embedding_device = os.getenv(
            "EMBEDDING_DEVICE",
            "cpu",
        ).strip()

        if not embedding_device:
            raise ConfigurationError("EMBEDDING_DEVICE cannot be empty")

        documentation_root = os.getenv(
            "DOCUMENTATION_ROOT",
            "/documentation",
        ).strip()

        if not documentation_root:
            raise ConfigurationError("DOCUMENTATION_ROOT cannot be empty")

        model_cache_dir = os.getenv(
            "MODEL_CACHE_DIR",
            "/models",
        ).strip()

        if not model_cache_dir:
            raise ConfigurationError("MODEL_CACHE_DIR cannot be empty")

        embedding_model = os.getenv(
            "EMBEDDING_MODEL",
            DEFAULT_EMBEDDING_MODEL,
        ).strip()

        if not embedding_model:
            raise ConfigurationError("EMBEDDING_MODEL cannot be empty")

        embedding_model_revision = os.getenv(
            "EMBEDDING_MODEL_REVISION",
            DEFAULT_EMBEDDING_MODEL_REVISION,
        ).strip()

        if not embedding_model_revision:
            raise ConfigurationError("EMBEDDING_MODEL_REVISION cannot be empty")

        return cls(
            db_host=os.getenv(
                "RAG_DB_HOST",
                "rag-db",
            ).strip(),
            db_port=integer_environment_variable(
                "RAG_DB_PORT",
                5432,
                minimum=1,
            ),
            db_name=os.getenv(
                "RAG_DB_NAME",
                "redmine_assistant",
            ).strip(),
            db_user=os.getenv(
                "RAG_DB_USER",
                "redmine_assistant",
            ).strip(),
            db_password=required_environment_variable("RAG_DB_PASSWORD"),
            documentation_root=documentation_root,
            top_k=configured_top_k,
            max_top_k=configured_max_top_k,
            embedding_model=embedding_model,
            embedding_model_revision=embedding_model_revision,
            embedding_dimension=integer_environment_variable(
                "EMBEDDING_DIMENSION",
                DEFAULT_EMBEDDING_DIMENSION,
                minimum=1,
            ),
            embedding_device=embedding_device,
            embedding_batch_size=integer_environment_variable(
                "EMBEDDING_BATCH_SIZE",
                32,
                minimum=1,
            ),
            embedding_normalize=boolean_environment_variable(
                "EMBEDDING_NORMALIZE",
                True,
            ),
            model_cache_dir=model_cache_dir,
        )


settings = Settings.from_environment()
