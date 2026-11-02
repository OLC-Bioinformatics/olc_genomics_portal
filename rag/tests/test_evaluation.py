#!/usr/bin/env python3

"""Tests for semantic-retrieval evaluation."""

from pathlib import Path

import pytest

from evaluation import (
    EvaluationError,
    EvaluationQuestion,
    contains_all_terms,
    evaluate_question,
    evaluate_questions,
    load_evaluation_questions,
    source_hit,
    source_requirement_hit,
)
from retrieval import SearchResult


def search_result(
    *,
    rank: int = 1,
    source_path: str = "analysis/mobsuite.md",
    heading_path: str = "MobSuite > What does it do?",
    content: str = "MobSuite detects plasmids.",
    score: float = 0.9,
    access_level: str = "standard",
) -> SearchResult:
    """Create a representative search result."""
    return SearchResult(
        rank=rank,
        chunk_key=f"{source_path}::{rank}",
        source_path=source_path,
        source_url=source_path,
        document_title=source_path,
        heading_path=heading_path,
        content=content,
        score=score,
        access_level=access_level,
    )


def evaluation_question(
    *,
    identifier: str = "plasmid-test",
    question: str = "Which tool detects plasmids?",
    expected_sources: tuple[str, ...] = (
        "analysis/mobsuite.md",
    ),
    expected_terms: tuple[str, ...] = ("MobSuite",),
    forbidden_terms: tuple[str, ...] = (),
    expected_heading_terms: tuple[str, ...] = (),
    should_abstain: bool = False,
    expected_access_level: str = "standard",
    evaluation_context: str | None = None,
    source_match: str = "any",
    minimum_expected_sources: int | None = None,
    maximum_top_score: float | None = None,
) -> EvaluationQuestion:
    """Create a representative evaluation question."""
    return EvaluationQuestion(
        identifier=identifier,
        question=question,
        category="tool_selection",
        enabled=True,
        expected_sources=expected_sources,
        expected_terms=expected_terms,
        forbidden_terms=forbidden_terms,
        expected_heading_terms=expected_heading_terms,
        should_abstain=should_abstain,
        expected_access_level=expected_access_level,
        evaluation_context=(
            evaluation_context
            if evaluation_context is not None
            else expected_access_level
        ),
        source_match=source_match,
        minimum_expected_sources=minimum_expected_sources,
        maximum_top_score=maximum_top_score,
    )


def test_load_evaluation_questions(tmp_path: Path) -> None:
    """Valid YAML questions are loaded."""
    path = tmp_path / "questions.yaml"
    path.write_text(
        """
version: 1
questions:
  - id: plasmid-test
    question: Which tool detects plasmids?
    category: tool_selection
    enabled: true
    expected_sources:
      - analysis/mobsuite.md
    expected_terms:
      - MobSuite
""",
        encoding="utf-8",
    )

    questions = load_evaluation_questions(path)

    assert len(questions) == 1
    assert questions[0].identifier == "plasmid-test"
    assert questions[0].expected_sources == (
        "analysis/mobsuite.md",
    )
    assert questions[0].source_match == "any"
    assert questions[0].evaluation_context == "standard"
    assert questions[0].forbidden_terms == ()


def test_loads_extended_question_fields(tmp_path: Path) -> None:
    """Extended source, access, forbidden-term, and score fields load."""
    path = tmp_path / "questions.yaml"
    path.write_text(
        """
questions:
  - id: comparison
    question: Compare two tools.
    source_match: all
    expected_sources:
      - analysis/first.md
      - analysis/second.md
    forbidden_terms:
      - retired.example.invalid
  - id: abstention
    question: Is this unsupported?
    expected_sources: []
    should_abstain: true
    maximum_top_score: 0.5
""",
        encoding="utf-8",
    )

    questions = load_evaluation_questions(path)

    assert questions[0].source_match == "all"
    assert questions[0].forbidden_terms == (
        "retired.example.invalid",
    )
    assert questions[1].maximum_top_score == pytest.approx(0.5)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    """Question identifiers must be unique."""
    path = tmp_path / "questions.yaml"
    path.write_text(
        """
questions:
  - id: duplicate
    question: First question?
    expected_sources:
      - first.md
  - id: duplicate
    question: Second question?
    expected_sources:
      - second.md
""",
        encoding="utf-8",
    )

    with pytest.raises(
        EvaluationError,
        match="Duplicate",
    ):
        load_evaluation_questions(path)


def test_answerable_question_requires_sources(
    tmp_path: Path,
) -> None:
    """Answerable questions must define expected sources."""
    path = tmp_path / "questions.yaml"
    path.write_text(
        """
questions:
  - id: invalid
    question: What is this?
    expected_sources: []
""",
        encoding="utf-8",
    )

    with pytest.raises(
        EvaluationError,
        match="expected_sources",
    ):
        load_evaluation_questions(path)


def test_all_source_match_requires_multiple_sources(
    tmp_path: Path,
) -> None:
    """All-source matching requires at least two expected sources."""
    path = tmp_path / "questions.yaml"
    path.write_text(
        """
questions:
  - id: invalid-all
    question: Compare tools.
    source_match: all
    expected_sources:
      - only-one.md
""",
        encoding="utf-8",
    )

    with pytest.raises(
        EvaluationError,
        match="at least two sources",
    ):
        load_evaluation_questions(path)


def test_at_least_source_match_requires_minimum(
    tmp_path: Path,
) -> None:
    """At-least matching requires a valid minimum source count."""
    path = tmp_path / "questions.yaml"
    path.write_text(
        """
questions:
  - id: invalid-minimum
    question: Select tools.
    source_match: at_least
    expected_sources:
      - first.md
      - second.md
""",
        encoding="utf-8",
    )

    with pytest.raises(
        EvaluationError,
        match="minimum_expected_sources",
    ):
        load_evaluation_questions(path)


def test_contains_all_terms_is_case_insensitive() -> None:
    """Expected-term matching ignores case."""
    assert contains_all_terms(
        "MobSuite detects PLASMIDS.",
        ("mobsuite", "plasmids"),
    )


def test_source_hit_uses_any_expected_source() -> None:
    """Any listed expected source can satisfy the compatibility metric."""
    results = [
        search_result(
            source_path="index.md",
        ),
        search_result(
            rank=2,
            source_path="analysis/mobsuite.md",
        ),
    ]

    assert not source_hit(
        results,
        ("analysis/mobsuite.md",),
        1,
    )
    assert source_hit(
        results,
        ("analysis/mobsuite.md",),
        3,
    )


def test_source_requirement_hit_supports_all() -> None:
    """All-source matching requires every expected source."""
    results = [
        search_result(source_path="analysis/first.md"),
        search_result(rank=2, source_path="analysis/second.md"),
    ]
    expected = (
        "analysis/first.md",
        "analysis/second.md",
    )

    assert not source_requirement_hit(
        results,
        expected,
        1,
        "all",
        None,
    )
    assert source_requirement_hit(
        results,
        expected,
        2,
        "all",
        None,
    )


def test_source_requirement_hit_supports_at_least() -> None:
    """At-least matching uses the configured minimum source count."""
    results = [
        search_result(source_path="analysis/first.md"),
        search_result(rank=2, source_path="analysis/second.md"),
    ]
    expected = (
        "analysis/first.md",
        "analysis/second.md",
        "analysis/third.md",
    )

    assert source_requirement_hit(
        results,
        expected,
        2,
        "at_least",
        2,
    )
    assert not source_requirement_hit(
        results,
        expected,
        2,
        "at_least",
        3,
    )


def test_evaluate_question_calculates_hits() -> None:
    """Per-question source and content metrics are calculated."""
    def fake_retriever(**kwargs):
        return [
            search_result(
                content="MobSuite detects plasmids.",
            )
        ]

    result = evaluate_question(
        question=evaluation_question(),
        top_k=5,
        retriever=fake_retriever,
    )

    assert result.source_hit_at_1 is True
    assert result.source_hit_at_3 is True
    assert result.source_hit_at_5 is True
    assert result.source_requirement_at_5 is True
    assert result.content_terms_hit is True
    assert result.internal_leakage is False


def test_expected_terms_are_scoped_to_expected_sources() -> None:
    """Expected terms must occur in retrieved expected-source chunks."""

    def fake_retriever(**kwargs):
        return [
            search_result(
                source_path="analysis/unrelated.md",
                heading_path="Unrelated > Results",
                content=("The unrelated result contains exclusive-marker-term."),
            ),
            search_result(
                rank=2,
                source_path="analysis/mobsuite.md",
                heading_path="MobSuite > Results",
                content=("This expected source does not contain the exclusive marker."),
            ),
        ]

    result = evaluate_question(
        question=evaluation_question(
            expected_terms=("exclusive-marker-term",),
        ),
        top_k=5,
        retriever=fake_retriever,
    )

    assert result.source_hit_at_5 is True
    assert result.content_terms_hit is False


def test_forbidden_terms_are_detected() -> None:
    """Forbidden terms found anywhere in retrieved content are reported."""
    def fake_retriever(**kwargs):
        return [
            search_result(
                content="Use retired.example.invalid for this service.",
            )
        ]

    result = evaluate_question(
        question=evaluation_question(
            forbidden_terms=("retired.example.invalid",),
        ),
        top_k=5,
        retriever=fake_retriever,
    )

    assert result.forbidden_terms_absent is False
    assert result.matched_forbidden_terms == (
        "retired.example.invalid",
    )


def test_internal_question_checks_both_access_modes() -> None:
    """Internal evaluations test filtering and authorized retrieval."""
    calls = []

    def fake_retriever(**kwargs):
        calls.append(kwargs["include_internal"])

        if kwargs["include_internal"]:
            return [
                search_result(
                    source_path="internal_only/merge.md",
                    access_level="internal",
                )
            ]

        return [
            search_result(
                source_path="analysis/snippy.md",
            )
        ]

    result = evaluate_question(
        question=evaluation_question(
            identifier="internal-merge",
            question="How do I use Merge?",
            expected_sources=("internal_only/merge.md",),
            expected_terms=(),
            expected_access_level="internal",
            evaluation_context="internal",
        ),
        top_k=5,
        retriever=fake_retriever,
    )

    assert calls == [False, True]
    assert result.internal_leakage is False
    assert result.internal_access_hit is True


def test_internal_leakage_is_detected() -> None:
    """Internal results in standard search are flagged."""
    def fake_retriever(**kwargs):
        return [
            search_result(
                source_path="internal_only/merge.md",
                access_level="internal",
            )
        ]

    result = evaluate_question(
        question=evaluation_question(),
        top_k=5,
        retriever=fake_retriever,
    )

    assert result.internal_leakage is True


def test_no_answer_question_records_score() -> None:
    """No-answer questions collect scores without source metrics."""
    def fake_retriever(**kwargs):
        return [
            search_result(score=0.51)
        ]

    result = evaluate_question(
        question=evaluation_question(
            identifier="unsupported",
            question="Can it fold proteins?",
            expected_sources=(),
            expected_terms=(),
            should_abstain=True,
        ),
        top_k=5,
        retriever=fake_retriever,
    )

    assert result.top_score == pytest.approx(0.51)
    assert result.source_hit_at_1 is None
    assert result.source_hit_at_5 is None
    assert result.abstention_pass is None


def test_no_answer_question_uses_score_threshold() -> None:
    """An abstention score threshold produces a pass/fail result."""
    def fake_retriever(**kwargs):
        return [search_result(score=0.51)]

    question = evaluation_question(
        identifier="unsupported",
        question="Can it fold proteins?",
        expected_sources=(),
        expected_terms=(),
        should_abstain=True,
    )

    passing = evaluate_question(
        question=question,
        top_k=5,
        retriever=fake_retriever,
        default_abstention_max_score=0.60,
    )
    failing = evaluate_question(
        question=question,
        top_k=5,
        retriever=fake_retriever,
        default_abstention_max_score=0.50,
    )

    assert passing.abstention_pass is True
    assert failing.abstention_pass is False


def test_evaluate_questions_calculates_rates(
    tmp_path: Path,
) -> None:
    """Aggregate evaluation rates are calculated."""
    questions = [
        evaluation_question(identifier="first"),
        evaluation_question(
            identifier="second",
            expected_sources=("analysis/geneseekr.md",),
            expected_terms=(),
        ),
    ]

    def fake_retriever(**kwargs):
        return [
            search_result(
                source_path="analysis/mobsuite.md",
            )
        ]

    summary = evaluate_questions(
        questions=questions,
        evaluation_path=tmp_path / "questions.yaml",
        top_k=5,
        retriever=fake_retriever,
    )

    assert summary.answerable_questions == 2
    assert summary.source_hit_at_1_count == 1
    assert summary.source_hit_at_1_rate == pytest.approx(0.5)
    assert summary.source_hit_at_5_rate == pytest.approx(0.5)
    assert summary.source_requirement_at_5_rate == pytest.approx(0.5)


def test_evaluation_requires_top_five() -> None:
    """Evaluation needs at least five results for Hit@5."""
    with pytest.raises(
        EvaluationError,
        match="at least 5",
    ):
        evaluate_questions(
            questions=[evaluation_question()],
            evaluation_path=Path("questions.yaml"),
            top_k=3,
            retriever=lambda **kwargs: [],
        )
