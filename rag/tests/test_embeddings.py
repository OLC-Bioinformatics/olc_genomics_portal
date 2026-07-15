#!/usr/bin/env python3

"""Tests for embedding generation and validation."""

# Standard library imports
import math

# Third-party imports
import numpy
import pytest

# Local imports
from embeddings import (
    EmbeddingError,
    EmbeddingService,
)


class FakeEmbeddingModel:
    """Simple SentenceTransformer-compatible test model."""

    max_seq_length = 512

    def __init__(
        self,
        dimension: int = 384,
    ) -> None:
        """Initialize the fake model."""
        self.dimension = dimension
        self.encode_calls: list[dict[str, object]] = []

    def get_embedding_dimension(self) -> int:
        """Return the configured fake-vector dimension."""
        return self.dimension

    def encode(
        self,
        texts,
        batch_size,
        show_progress_bar,
        convert_to_numpy,
        normalize_embeddings,
    ):
        """Return deterministic fake vectors."""
        self.encode_calls.append(
            {
                "texts": list(texts),
                "batch_size": batch_size,
                "show_progress_bar": show_progress_bar,
                "convert_to_numpy": convert_to_numpy,
                "normalize_embeddings": normalize_embeddings,
            }
        )

        vectors = []

        for text_index, _ in enumerate(texts, start=1):
            vector = numpy.full(
                self.dimension,
                float(text_index),
                dtype=numpy.float32,
            )

            if normalize_embeddings:
                vector = vector / numpy.linalg.norm(vector)

            vectors.append(vector)

        return numpy.asarray(vectors)


def test_embed_documents_returns_expected_vectors() -> None:
    """Document embedding returns one vector per input."""
    fake_model = FakeEmbeddingModel()
    service = EmbeddingService(model=fake_model)

    vectors = service.embed_documents(
        [
            "Document: MobSuite\n\nDetects plasmids.",
            "Document: GeneSeekr\n\nDetects gene targets.",
        ]
    )

    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert len(vectors[1]) == 384
    assert all(
        isinstance(value, float)
        for value in vectors[0]
    )


def test_embed_documents_returns_empty_list_for_empty_input() -> None:
    """Empty document collections require no model inference."""
    fake_model = FakeEmbeddingModel()
    service = EmbeddingService(model=fake_model)

    vectors = service.embed_documents([])

    assert vectors == []
    assert fake_model.encode_calls == []


def test_embed_query_returns_one_vector() -> None:
    """Query embedding returns a single vector."""
    service = EmbeddingService(
        model=FakeEmbeddingModel()
    )

    vector = service.embed_query(
        "Which automator detects plasmids?"
    )

    assert len(vector) == 384
    assert all(
        isinstance(value, float)
        for value in vector
    )


def test_embedding_inputs_are_trimmed() -> None:
    """Surrounding whitespace is removed before inference."""
    fake_model = FakeEmbeddingModel()
    service = EmbeddingService(model=fake_model)

    service.embed_query(
        "  Which automator detects plasmids?  "
    )

    assert fake_model.encode_calls[0]["texts"] == [
        "Which automator detects plasmids?"
    ]


def test_blank_query_is_rejected() -> None:
    """Blank queries cannot be embedded."""
    service = EmbeddingService(
        model=FakeEmbeddingModel()
    )

    with pytest.raises(
        EmbeddingError,
        match="cannot be blank",
    ):
        service.embed_query("   ")


def test_non_string_document_is_rejected() -> None:
    """Every embedding input must be a string."""
    service = EmbeddingService(
        model=FakeEmbeddingModel()
    )

    with pytest.raises(
        EmbeddingError,
        match="must be a string",
    ):
        service.embed_documents(
            ["Valid document", None]
        )


def test_generated_vectors_are_normalized() -> None:
    """Configured normalization produces unit-length vectors."""
    service = EmbeddingService(
        model=FakeEmbeddingModel()
    )

    vector = service.embed_query(
        "Which automator detects plasmids?"
    )

    magnitude = math.sqrt(
        sum(value * value for value in vector)
    )

    assert magnitude == pytest.approx(
        1.0,
        abs=1e-5,
    )


def test_vector_dimension_mismatch_is_rejected() -> None:
    """Unexpected vector dimensions fail validation."""
    service = EmbeddingService(
        model=FakeEmbeddingModel(dimension=128)
    )

    with pytest.raises(
        EmbeddingError,
        match="dimension mismatch",
    ):
        service.embed_query(
            "Which automator detects plasmids?"
        )


class WrongCountModel(FakeEmbeddingModel):
    """Fake model returning too few vectors."""

    def encode(
        self,
        texts,
        batch_size,
        show_progress_bar,
        convert_to_numpy,
        normalize_embeddings,
    ):
        """Return no vectors regardless of supplied input."""
        return numpy.empty(
            (0, self.dimension),
            dtype=numpy.float32,
        )


def test_vector_count_mismatch_is_rejected() -> None:
    """The model must return one vector per input."""
    service = EmbeddingService(
        model=WrongCountModel()
    )

    with pytest.raises(
        EmbeddingError,
        match="count mismatch",
    ):
        service.embed_documents(
            [
                "First document",
                "Second document",
            ]
        )


class FailingModel(FakeEmbeddingModel):
    """Fake model that raises during inference."""

    def encode(
        self,
        texts,
        batch_size,
        show_progress_bar,
        convert_to_numpy,
        normalize_embeddings,
    ):
        """Simulate a model inference failure."""
        raise RuntimeError("Simulated model failure")


def test_model_inference_failure_is_wrapped() -> None:
    """Model exceptions are converted into EmbeddingError."""
    service = EmbeddingService(
        model=FailingModel()
    )

    with pytest.raises(
        EmbeddingError,
        match="Failed to generate document embeddings",
    ):
        service.embed_query(
            "Which automator detects plasmids?"
        )
