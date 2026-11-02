#!/usr/bin/env python3

"""Embedding generation for RedmineAssistant documentation retrieval."""

# Standard library imports
from __future__ import annotations
from collections.abc import Sequence
import logging
from threading import Lock
from typing import Any

# Third-party imports
from sentence_transformers import SentenceTransformer

# Local imports
from config import settings


LOGGER = logging.getLogger("redmine-assistant-embeddings")


class EmbeddingError(RuntimeError):
    """Raised when embedding generation or validation fails."""


class EmbeddingService:
    """Generate normalized embeddings for documents and search queries."""

    def __init__(
        self,
        model: Any | None = None,
    ) -> None:
        """
        Initialize the embedding service.

        The model is loaded lazily unless an existing model instance is
        supplied. Model injection is primarily useful for unit tests.

        Args:
            model: Optional preloaded SentenceTransformer-compatible model.
        """
        self._model = model
        self._load_lock = Lock()

    @property
    def model_name(self) -> str:
        """Return the configured embedding-model identifier."""
        return settings.embedding_model

    @property
    def model_revision(self) -> str:
        """Return the configured embedding-model revision."""
        return settings.embedding_model_revision

    @property
    def dimension(self) -> int:
        """Return the required embedding-vector dimension."""
        return settings.embedding_dimension

    @property
    def normalize_embeddings(self) -> bool:
        """Return whether generated embeddings are normalized."""
        return settings.embedding_normalize

    def _load_model(self) -> Any:
        """
        Load and cache the configured Sentence Transformer model.

        Returns:
            Loaded SentenceTransformer-compatible model.

        Raises:
            EmbeddingError: If the model cannot be loaded or has the wrong
                embedding dimension.
        """
        if self._model is not None:
            return self._model

        with self._load_lock:
            if self._model is not None:
                return self._model

            LOGGER.info(
                "Loading embedding model %s at revision %s on %s",
                settings.embedding_model,
                settings.embedding_model_revision,
                settings.embedding_device,
            )

            try:
                model = SentenceTransformer(
                    settings.embedding_model,
                    revision=settings.embedding_model_revision,
                    cache_folder=settings.model_cache_dir,
                    device=settings.embedding_device,
                )
            except Exception as exc:
                raise EmbeddingError(
                    "Failed to load the configured embedding model"
                ) from exc

            try:
                actual_dimension = model.get_embedding_dimension()
            except Exception as exc:
                raise EmbeddingError(
                    "Could not determine the model embedding dimension"
                ) from exc

            if actual_dimension != settings.embedding_dimension:
                raise EmbeddingError(
                    "Embedding dimension mismatch: "
                    f"configured={settings.embedding_dimension}, "
                    f"model={actual_dimension}"
                )

            LOGGER.info(
                "Embedding model loaded successfully: "
                "dimension=%s, max_sequence_length=%s",
                actual_dimension,
                getattr(model, "max_seq_length", "unknown"),
            )

            self._model = model

        return self._model

    def _validate_texts(
        self,
        texts: Sequence[str],
    ) -> list[str]:
        """
        Validate and normalize embedding inputs.

        Args:
            texts: Text values to validate.

        Returns:
            Validated text values with surrounding whitespace removed.

        Raises:
            EmbeddingError: If an input is not a string or is blank.
        """
        validated_texts: list[str] = []

        for index, text in enumerate(texts):
            if not isinstance(text, str):
                raise EmbeddingError(
                    f"Embedding input at index {index} must be a string"
                )

            normalized = text.strip()

            if not normalized:
                raise EmbeddingError(
                    f"Embedding input at index {index} cannot be blank"
                )

            validated_texts.append(normalized)

        return validated_texts

    def _validate_vectors(
        self,
        vectors: Any,
        expected_count: int,
    ) -> list[list[float]]:
        """
        Validate and convert generated vectors.

        Args:
            vectors: Model output containing one vector per input.
            expected_count: Number of expected vectors.

        Returns:
            Embeddings represented as ordinary Python float lists.

        Raises:
            EmbeddingError: If vector count or dimensions are invalid.
        """
        try:
            vector_list = vectors.tolist()
        except AttributeError:
            vector_list = list(vectors)

        if len(vector_list) != expected_count:
            raise EmbeddingError(
                "Embedding count mismatch: "
                f"expected={expected_count}, "
                f"actual={len(vector_list)}"
            )

        validated_vectors: list[list[float]] = []

        for index, vector in enumerate(vector_list):
            converted_vector = [
                float(value)
                for value in vector
            ]

            if len(converted_vector) != self.dimension:
                raise EmbeddingError(
                    "Embedding dimension mismatch at vector "
                    f"{index}: expected={self.dimension}, "
                    f"actual={len(converted_vector)}"
                )

            validated_vectors.append(converted_vector)

        return validated_vectors

    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> list[list[float]]:
        """
        Generate embeddings for documentation chunks.

        Args:
            texts: Context-rich documentation chunk text.

        Returns:
            One embedding vector per input text.

        Raises:
            EmbeddingError: If validation or model inference fails.
        """
        if not texts:
            return []

        validated_texts = self._validate_texts(texts)
        model = self._load_model()

        try:
            vectors = model.encode(
                validated_texts,
                batch_size=settings.embedding_batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=settings.embedding_normalize,
            )
        except Exception as exc:
            raise EmbeddingError(
                "Failed to generate document embeddings"
            ) from exc

        return self._validate_vectors(
            vectors=vectors,
            expected_count=len(validated_texts),
        )

    def embed_query(
        self,
        query: str,
    ) -> list[float]:
        """
        Generate an embedding for one search query.

        Args:
            query: User search text.

        Returns:
            One embedding vector.

        Raises:
            EmbeddingError: If the query is blank or inference fails.
        """
        vectors = self.embed_documents([query])

        return vectors[0]


embedding_service = EmbeddingService()
