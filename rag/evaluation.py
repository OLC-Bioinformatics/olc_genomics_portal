#!/usr/bin/env python3

"""Evaluation of RedmineAssistant semantic retrieval."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import statistics
from typing import Any

import yaml

from retrieval import SearchResult, retrieve_chunks


LOGGER = logging.getLogger("redmine-assistant-evaluation")

DEFAULT_EVALUATION_PATH = Path(
    "/app/tests/evaluation_questions.yaml"
)


class EvaluationError(RuntimeError):
    """Raised when an evaluation file or evaluation run is invalid."""


@dataclass(frozen=True)
class EvaluationQuestion:
    """One semantic-retrieval evaluation question."""

    identifier: str
    question: str
    category: str
    enabled: bool
    expected_sources: tuple[str, ...]
    expected_terms: tuple[str, ...]
    expected_heading_terms: tuple[str, ...]
    should_abstain: bool
    expected_access_level: str
    notes: str | None = None


@dataclass(frozen=True)
class RankedResult:
    """Serializable representation of a retrieved chunk."""

    rank: int
    source_path: str
    document_title: str
    heading_path: str
    score: float
    access_level: str
    chunk_key: str


@dataclass
class QuestionEvaluation:
    """Evaluation result for one question."""

    identifier: str
    question: str
    category: str
    expected_sources: tuple[str, ...]
    expected_terms: tuple[str, ...]
    expected_heading_terms: tuple[str, ...]
    should_abstain: bool
    expected_access_level: str

    source_hit_at_1: bool | None = None
    source_hit_at_3: bool | None = None
    source_hit_at_5: bool | None = None
    heading_terms_hit: bool | None = None
    content_terms_hit: bool | None = None
    internal_leakage: bool = False
    internal_access_hit: bool | None = None
    top_score: float | None = None

    results: list[RankedResult] = field(default_factory=list)
    error: str | None = None

    @property
    def failed_source_at_5(self) -> bool:
        """Return whether an answerable question missed at rank five."""
        return self.source_hit_at_5 is False


@dataclass
class EvaluationSummary:
    """Aggregate metrics for one retrieval evaluation run."""

    evaluation_path: str
    top_k: int
    category_filter: str | None
    started_at: str
    completed_at: str

    questions_loaded: int
    questions_evaluated: int
    answerable_questions: int
    no_answer_questions: int
    internal_questions: int
    errors: int

    source_hit_at_1_count: int
    source_hit_at_3_count: int
    source_hit_at_5_count: int

    source_hit_at_1_rate: float | None
    source_hit_at_3_rate: float | None
    source_hit_at_5_rate: float | None

    heading_questions: int
    heading_hit_count: int
    heading_hit_rate: float | None

    term_questions: int
    term_hit_count: int
    term_hit_rate: float | None

    internal_leakage_count: int
    internal_access_hit_count: int
    internal_access_hit_rate: float | None

    no_answer_mean_top_score: float | None
    no_answer_max_top_score: float | None

    category_counts: dict[str, int]
    results: list[QuestionEvaluation]


Retriever = Callable[..., list[SearchResult]]


def string_tuple(
    value: Any,
    field_name: str,
    question_id: str,
) -> tuple[str, ...]:
    """
    Validate and convert a YAML string-list field.

    Args:
        value: Raw YAML value.
        field_name: Field being validated.
        question_id: Question identifier for error reporting.

    Returns:
        Validated strings as a tuple.

    Raises:
        EvaluationError: If the value is not a list of non-empty strings.
    """
    if value is None:
        return ()

    if not isinstance(value, list):
        raise EvaluationError(
            f"Question {question_id!r}: {field_name} must be a list"
        )

    converted: list[str] = []

    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise EvaluationError(
                f"Question {question_id!r}: {field_name} must "
                "contain only non-empty strings"
            )

        converted.append(item.strip())

    return tuple(converted)


def parse_evaluation_question(
    raw_question: Any,
    question_number: int,
) -> EvaluationQuestion:
    """
    Parse and validate one YAML question.

    Args:
        raw_question: Raw YAML question mapping.
        question_number: One-based position in the file.

    Returns:
        Validated evaluation question.

    Raises:
        EvaluationError: If a required field is invalid.
    """
    if not isinstance(raw_question, dict):
        raise EvaluationError(
            f"Question {question_number} must be a mapping"
        )

    identifier = raw_question.get("id")

    if not isinstance(identifier, str) or not identifier.strip():
        raise EvaluationError(
            f"Question {question_number} has an invalid id"
        )

    identifier = identifier.strip()
    question = raw_question.get("question")

    if not isinstance(question, str) or not question.strip():
        raise EvaluationError(
            f"Question {identifier!r} has invalid question text"
        )

    category = raw_question.get("category", "uncategorized")

    if not isinstance(category, str) or not category.strip():
        raise EvaluationError(
            f"Question {identifier!r} has an invalid category"
        )

    enabled = raw_question.get("enabled", True)

    if not isinstance(enabled, bool):
        raise EvaluationError(
            f"Question {identifier!r}: enabled must be Boolean"
        )

    should_abstain = raw_question.get("should_abstain", False)

    if not isinstance(should_abstain, bool):
        raise EvaluationError(
            f"Question {identifier!r}: should_abstain must be Boolean"
        )

    expected_access_level = raw_question.get(
        "expected_access_level",
        "standard",
    )

    if expected_access_level not in {"standard", "internal"}:
        raise EvaluationError(
            f"Question {identifier!r}: expected_access_level must be "
            "'standard' or 'internal'"
        )

    notes = raw_question.get("notes")

    if notes is not None and not isinstance(notes, str):
        raise EvaluationError(
            f"Question {identifier!r}: notes must be a string"
        )

    expected_sources = string_tuple(
        raw_question.get("expected_sources", []),
        "expected_sources",
        identifier,
    )

    if not should_abstain and not expected_sources:
        raise EvaluationError(
            f"Question {identifier!r} must define expected_sources "
            "unless should_abstain is true"
        )

    return EvaluationQuestion(
        identifier=identifier,
        question=question.strip(),
        category=category.strip(),
        enabled=enabled,
        expected_sources=expected_sources,
        expected_terms=string_tuple(
            raw_question.get("expected_terms", []),
            "expected_terms",
            identifier,
        ),
        expected_heading_terms=string_tuple(
            raw_question.get("expected_heading_terms", []),
            "expected_heading_terms",
            identifier,
        ),
        should_abstain=should_abstain,
        expected_access_level=expected_access_level,
        notes=notes.strip() if notes else None,
    )


def load_evaluation_questions(
    path: Path,
) -> list:
    """
    Load and validate evaluation questions from YAML.

    Args:
        path: Evaluation YAML path.

    Returns:
        Enabled and disabled validated questions.

    Raises:
        EvaluationError: If the file cannot be read or is invalid.
    """
    if not path.exists():
        raise EvaluationError(
            f"Evaluation question file does not exist: {path}"
        )

    if not path.is_file():
        raise EvaluationError(
            f"Evaluation question path is not a file: {path}"
        )

    try:
        raw_document = yaml.safe_load(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise EvaluationError(
            f"Could not load evaluation questions: {path}"
        ) from exc

    if not isinstance(raw_document, dict):
        raise EvaluationError(
            "Evaluation YAML root must be a mapping"
        )

    raw_questions = raw_document.get("questions")

    if not isinstance(raw_questions, list):
        raise EvaluationError(
            "Evaluation YAML must contain a questions list"
        )

    questions = [
        parse_evaluation_question(
            raw_question=raw_question,
            question_number=question_number,
        )
        for question_number, raw_question in enumerate(
            raw_questions,
            start=1,
        )
    ]

    identifiers = [
        question.identifier
        for question in questions
    ]

    duplicate_identifiers = sorted(
        identifier
        for identifier, count in Counter(identifiers).items()
        if count > 1
    )

    if duplicate_identifiers:
        raise EvaluationError(
            "Duplicate evaluation question IDs: "
            + ", ".join(duplicate_identifiers)
        )

    return questions


def contains_all_terms(
    text: str,
    terms: tuple[str, ...],
) -> bool:
    """
    Determine whether text contains all expected terms.

    Matching is case-insensitive.

    Args:
        text: Combined retrieved text.
        terms: Expected terms.

    Returns:
        True if every expected term occurs.
    """
    normalized_text = text.casefold()

    return all(
        term.casefold() in normalized_text
        for term in terms
    )


def source_hit(
    results: list[SearchResult],
    expected_sources: tuple[str, ...],
    cutoff: int,
) -> bool:
    """
    Determine whether an expected source occurs before a rank cutoff.

    Args:
        results: Ranked retrieval results.
        expected_sources: Acceptable source paths.
        cutoff: Maximum rank to inspect.

    Returns:
        True if any expected source occurs in the inspected results.
    """
    expected_source_set = set(expected_sources)

    return any(
        result.source_path in expected_source_set
        for result in results[:cutoff]
    )


def serialize_results(
    results: list[SearchResult],
) -> list[RankedResult]:
    """
    Convert search results into report-safe values.

    Args:
        results: Retrieval results.

    Returns:
        Serializable ranked results.
    """
    return [
        RankedResult(
            rank=result.rank,
            source_path=result.source_path,
            document_title=result.document_title,
            heading_path=result.heading_path,
            score=result.score,
            access_level=result.access_level,
            chunk_key=result.chunk_key,
        )
        for result in results
    ]


def evaluate_question(
    question: EvaluationQuestion,
    top_k: int,
    retriever: Retriever = retrieve_chunks,
) -> QuestionEvaluation:
    """
    Evaluate retrieval for one question.

    Internal questions are first searched without internal access to check
    for leakage, then searched with internal access for expected-source
    evaluation.

    Args:
        question: Evaluation question.
        top_k: Number of results to retrieve.
        retriever: Retrieval function, injectable for tests.

    Returns:
        Per-question evaluation.
    """
    evaluation = QuestionEvaluation(
        identifier=question.identifier,
        question=question.question,
        category=question.category,
        expected_sources=question.expected_sources,
        expected_terms=question.expected_terms,
        expected_heading_terms=question.expected_heading_terms,
        should_abstain=question.should_abstain,
        expected_access_level=question.expected_access_level,
    )

    try:
        standard_results = retriever(
            query=question.question,
            limit=top_k,
            include_internal=False,
        )

        evaluation.internal_leakage = any(
            result.access_level == "internal"
            or result.source_path.startswith("internal_only/")
            for result in standard_results
        )

        if question.expected_access_level == "internal":
            results = retriever(
                query=question.question,
                limit=top_k,
                include_internal=True,
            )

            evaluation.internal_access_hit = source_hit(
                results=results,
                expected_sources=question.expected_sources,
                cutoff=top_k,
            )
        else:
            results = standard_results

        evaluation.results = serialize_results(results)

        if results:
            evaluation.top_score = results[0].score

        if not question.should_abstain:
            evaluation.source_hit_at_1 = source_hit(
                results,
                question.expected_sources,
                1,
            )
            evaluation.source_hit_at_3 = source_hit(
                results,
                question.expected_sources,
                min(3, top_k),
            )
            evaluation.source_hit_at_5 = source_hit(
                results,
                question.expected_sources,
                min(5, top_k),
            )

        if question.expected_heading_terms:
            combined_headings = "\n".join(
                result.heading_path
                for result in results
            )

            evaluation.heading_terms_hit = contains_all_terms(
                combined_headings,
                question.expected_heading_terms,
            )

        if question.expected_terms:
            combined_content = "\n".join(
                (
                    result.document_title
                    + "\n"
                    + result.heading_path
                    + "\n"
                    + result.content
                )
                for result in results
            )

            evaluation.content_terms_hit = contains_all_terms(
                combined_content,
                question.expected_terms,
            )

    except Exception as exc:
        LOGGER.exception(
            "Evaluation failed for question %s",
            question.identifier,
        )
        evaluation.error = str(exc)

    return evaluation


def safe_rate(
    numerator: int,
    denominator: int,
) -> float | None:
    """
    Calculate a rate when the denominator is nonzero.

    Args:
        numerator: Successful count.
        denominator: Total count.

    Returns:
        Fraction from zero to one, or None.
    """
    if denominator == 0:
        return None

    return numerator / denominator


def evaluate_questions(
    questions: list[EvaluationQuestion],
    evaluation_path: Path,
    top_k: int = 5,
    category: str | None = None,
    retriever: Retriever = retrieve_chunks,
) -> EvaluationSummary:
    """
    Evaluate all enabled questions.

    Args:
        questions: Loaded evaluation questions.
        evaluation_path: Source YAML path.
        top_k: Number of retrieval results per question.
        category: Optional category filter.
        retriever: Retrieval function, injectable for tests.

    Returns:
        Aggregate evaluation summary.

    Raises:
        EvaluationError: If top_k is invalid or no questions are selected.
    """
    if top_k < 5:
        raise EvaluationError(
            "Evaluation top_k must be at least 5 to calculate Hit@5"
        )

    selected_questions = [
        question
        for question in questions
        if question.enabled
        and (
            category is None
            or question.category == category
        )
    ]

    if not selected_questions:
        raise EvaluationError(
            "No enabled evaluation questions matched the filters"
        )

    started = datetime.now(timezone.utc)

    results = [
        evaluate_question(
            question=question,
            top_k=top_k,
            retriever=retriever,
        )
        for question in selected_questions
    ]

    completed = datetime.now(timezone.utc)

    answerable_results = [
        result
        for result in results
        if not result.should_abstain
        and result.error is None
    ]

    no_answer_results = [
        result
        for result in results
        if result.should_abstain
        and result.error is None
    ]

    heading_results = [
        result
        for result in results
        if result.heading_terms_hit is not None
        and result.error is None
    ]

    term_results = [
        result
        for result in results
        if result.content_terms_hit is not None
        and result.error is None
    ]

    internal_results = [
        result
        for result in results
        if result.expected_access_level == "internal"
        and result.error is None
    ]

    no_answer_scores = [
        result.top_score
        for result in no_answer_results
        if result.top_score is not None
    ]

    hit_at_1_count = sum(
        result.source_hit_at_1 is True
        for result in answerable_results
    )
    hit_at_3_count = sum(
        result.source_hit_at_3 is True
        for result in answerable_results
    )
    hit_at_5_count = sum(
        result.source_hit_at_5 is True
        for result in answerable_results
    )

    heading_hit_count = sum(
        result.heading_terms_hit is True
        for result in heading_results
    )
    term_hit_count = sum(
        result.content_terms_hit is True
        for result in term_results
    )
    internal_access_hit_count = sum(
        result.internal_access_hit is True
        for result in internal_results
    )

    return EvaluationSummary(
        evaluation_path=str(evaluation_path),
        top_k=top_k,
        category_filter=category,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        questions_loaded=len(questions),
        questions_evaluated=len(results),
        answerable_questions=len(answerable_results),
        no_answer_questions=len(no_answer_results),
        internal_questions=len(internal_results),
        errors=sum(
            result.error is not None
            for result in results
        ),
        source_hit_at_1_count=hit_at_1_count,
        source_hit_at_3_count=hit_at_3_count,
        source_hit_at_5_count=hit_at_5_count,
        source_hit_at_1_rate=safe_rate(
            hit_at_1_count,
            len(answerable_results),
        ),
        source_hit_at_3_rate=safe_rate(
            hit_at_3_count,
            len(answerable_results),
        ),
        source_hit_at_5_rate=safe_rate(
            hit_at_5_count,
            len(answerable_results),
        ),
        heading_questions=len(heading_results),
        heading_hit_count=heading_hit_count,
        heading_hit_rate=safe_rate(
            heading_hit_count,
            len(heading_results),
        ),
        term_questions=len(term_results),
        term_hit_count=term_hit_count,
        term_hit_rate=safe_rate(
            term_hit_count,
            len(term_results),
        ),
        internal_leakage_count=sum(
            result.internal_leakage
            for result in results
        ),
        internal_access_hit_count=internal_access_hit_count,
        internal_access_hit_rate=safe_rate(
            internal_access_hit_count,
            len(internal_results),
        ),
        no_answer_mean_top_score=(
            statistics.mean(no_answer_scores)
            if no_answer_scores
            else None
        ),
        no_answer_max_top_score=(
            max(no_answer_scores)
            if no_answer_scores
            else None
        ),
        category_counts=dict(
            sorted(
                Counter(
                    result.category
                    for result in results
                ).items()
            )
        ),
        results=results,
    )


def percentage(value: float | None) -> str:
    """Format an optional fraction as a percentage."""
    if value is None:
        return "n/a"

    return f"{value * 100:.1f}%"


def print_evaluation_summary(
    summary: EvaluationSummary,
    show_failures: bool = True,
) -> None:
    """
    Print an evaluation report.

    Args:
        summary: Completed evaluation.
        show_failures: Print failed Hit@5 and error details.
    """
    print("Retrieval evaluation")
    print("====================")
    print(f"Questions loaded: {summary.questions_loaded}")
    print(f"Questions evaluated: {summary.questions_evaluated}")
    print(f"Answerable questions: {summary.answerable_questions}")
    print(f"No-answer questions: {summary.no_answer_questions}")
    print(f"Internal questions: {summary.internal_questions}")
    print(f"Errors: {summary.errors}")

    print()
    print("Source retrieval:")
    print(
        f"- Hit@1: {summary.source_hit_at_1_count}/"
        f"{summary.answerable_questions} "
        f"({percentage(summary.source_hit_at_1_rate)})"
    )
    print(
        f"- Hit@3: {summary.source_hit_at_3_count}/"
        f"{summary.answerable_questions} "
        f"({percentage(summary.source_hit_at_3_rate)})"
    )
    print(
        f"- Hit@5: {summary.source_hit_at_5_count}/"
        f"{summary.answerable_questions} "
        f"({percentage(summary.source_hit_at_5_rate)})"
    )

    print()
    print("Expected context:")
    print(
        f"- Heading terms: {summary.heading_hit_count}/"
        f"{summary.heading_questions} "
        f"({percentage(summary.heading_hit_rate)})"
    )
    print(
        f"- Content terms: {summary.term_hit_count}/"
        f"{summary.term_questions} "
        f"({percentage(summary.term_hit_rate)})"
    )

    print()
    print("Access control:")
    print(
        "- Standard searches leaking internal chunks: "
        f"{summary.internal_leakage_count}"
    )
    print(
        f"- Internal retrieval: "
        f"{summary.internal_access_hit_count}/"
        f"{summary.internal_questions} "
        f"({percentage(summary.internal_access_hit_rate)})"
    )

    print()
    print("No-answer score distribution:")
    print(
        "- Mean top score: "
        + (
            f"{summary.no_answer_mean_top_score:.4f}"
            if summary.no_answer_mean_top_score is not None
            else "n/a"
        )
    )
    print(
        "- Maximum top score: "
        + (
            f"{summary.no_answer_max_top_score:.4f}"
            if summary.no_answer_max_top_score is not None
            else "n/a"
        )
    )

    print()
    print("Categories:")

    for category, count in summary.category_counts.items():
        print(f"- {category}: {count}")

    if not show_failures:
        return

    failed_results = [
        result
        for result in summary.results
        if result.failed_source_at_5
    ]

    error_results = [
        result
        for result in summary.results
        if result.error is not None
    ]

    if failed_results:
        print()
        print("Failed source Hit@5:")

        for result in failed_results:
            print()
            print(f"- {result.identifier}")
            print(f"  Question: {result.question}")
            print(
                "  Expected: "
                + ", ".join(result.expected_sources)
            )
            print("  Returned:")

            for retrieved in result.results:
                print(
                    f"    {retrieved.rank}. "
                    f"{retrieved.source_path} "
                    f"({retrieved.score:.4f})"
                )
                print(
                    f"       {retrieved.heading_path}"
                )

    if error_results:
        print()
        print("Evaluation errors:")

        for result in error_results:
            print(
                f"- {result.identifier}: {result.error}"
            )


def write_json_report(
    summary: EvaluationSummary,
    path: Path,
) -> None:
    """
    Write a complete evaluation report as JSON.

    Args:
        summary: Completed evaluation.
        path: Destination JSON path.
    """
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            asdict(summary),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
