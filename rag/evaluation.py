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
DEFAULT_EVALUATION_PATH = Path("/app/tests/evaluation_questions.yaml")
SOURCE_MATCH_MODES = frozenset({"any", "all", "at_least"})
EVALUATION_CONTEXTS = frozenset({"standard", "internal"})


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
    forbidden_terms: tuple[str, ...]
    expected_heading_terms: tuple[str, ...]
    should_abstain: bool
    expected_access_level: str
    evaluation_context: str
    source_match: str
    minimum_expected_sources: int | None
    maximum_top_score: float | None
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
    forbidden_terms: tuple[str, ...]
    expected_heading_terms: tuple[str, ...]
    should_abstain: bool
    expected_access_level: str
    evaluation_context: str
    source_match: str
    minimum_expected_sources: int | None
    maximum_top_score: float | None

    # Traditional any-relevant-source retrieval metrics.
    source_hit_at_1: bool | None = None
    source_hit_at_3: bool | None = None
    source_hit_at_5: bool | None = None

    # Strict per-question source requirement (any/all/at_least).
    source_requirement_at_1: bool | None = None
    source_requirement_at_3: bool | None = None
    source_requirement_at_5: bool | None = None

    heading_terms_hit: bool | None = None
    content_terms_hit: bool | None = None
    forbidden_terms_absent: bool | None = None
    matched_forbidden_terms: tuple[str, ...] = ()
    abstention_pass: bool | None = None
    internal_leakage: bool = False
    internal_access_hit: bool | None = None
    top_score: float | None = None

    results: list[RankedResult] = field(default_factory=list)
    error: str | None = None

    @property
    def failed_source_at_5(self) -> bool:
        return self.source_hit_at_5 is False

    @property
    def failed_source_requirement_at_5(self) -> bool:
        return self.source_requirement_at_5 is False

    @property
    def failed_content_terms(self) -> bool:
        return self.content_terms_hit is False

    @property
    def failed_heading_terms(self) -> bool:
        return self.heading_terms_hit is False

    @property
    def failed_forbidden_terms(self) -> bool:
        return self.forbidden_terms_absent is False

    @property
    def failed_internal_access(self) -> bool:
        return self.internal_access_hit is False

    @property
    def failed_abstention(self) -> bool:
        return self.abstention_pass is False


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

    source_requirement_at_1_count: int
    source_requirement_at_3_count: int
    source_requirement_at_5_count: int
    source_requirement_at_1_rate: float | None
    source_requirement_at_3_rate: float | None
    source_requirement_at_5_rate: float | None

    heading_questions: int
    heading_hit_count: int
    heading_hit_rate: float | None
    term_questions: int
    term_hit_count: int
    term_hit_rate: float | None
    forbidden_term_questions: int
    forbidden_terms_absent_count: int
    forbidden_terms_absent_rate: float | None

    internal_leakage_count: int
    internal_access_hit_count: int
    internal_access_hit_rate: float | None

    abstention_questions_scored: int
    abstention_pass_count: int
    abstention_pass_rate: float | None
    no_answer_mean_top_score: float | None
    no_answer_max_top_score: float | None

    category_counts: dict[str, int]
    results: list[QuestionEvaluation]


Retriever = Callable[..., list[SearchResult]]


def string_tuple(value: Any, field_name: str, question_id: str) -> tuple[str, ...]:
    """Validate and convert a YAML string-list field."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise EvaluationError(f"Question {question_id!r}: {field_name} must be a list")
    converted: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise EvaluationError(
                f"Question {question_id!r}: {field_name} must contain only non-empty strings"
            )
        converted.append(item.strip())
    return tuple(converted)


def optional_float(value: Any, field_name: str, question_id: str) -> float | None:
    """Validate an optional numeric field."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"Question {question_id!r}: {field_name} must be numeric")
    converted = float(value)
    if not 0.0 <= converted <= 1.0:
        raise EvaluationError(f"Question {question_id!r}: {field_name} must be between 0 and 1")
    return converted


def parse_evaluation_question(raw_question: Any, question_number: int) -> EvaluationQuestion:
    """Parse and validate one YAML question."""
    if not isinstance(raw_question, dict):
        raise EvaluationError(f"Question {question_number} must be a mapping")

    identifier = raw_question.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise EvaluationError(f"Question {question_number} has an invalid id")
    identifier = identifier.strip()

    question = raw_question.get("question")
    if not isinstance(question, str) or not question.strip():
        raise EvaluationError(f"Question {identifier!r} has invalid question text")

    category = raw_question.get("category", "uncategorized")
    if not isinstance(category, str) or not category.strip():
        raise EvaluationError(f"Question {identifier!r} has an invalid category")

    enabled = raw_question.get("enabled", True)
    if not isinstance(enabled, bool):
        raise EvaluationError(f"Question {identifier!r}: enabled must be Boolean")

    should_abstain = raw_question.get("should_abstain", False)
    if not isinstance(should_abstain, bool):
        raise EvaluationError(f"Question {identifier!r}: should_abstain must be Boolean")

    expected_access_level = raw_question.get("expected_access_level", "standard")
    if expected_access_level not in EVALUATION_CONTEXTS:
        raise EvaluationError(
            f"Question {identifier!r}: expected_access_level must be 'standard' or 'internal'"
        )

    evaluation_context = raw_question.get("evaluation_context", expected_access_level)
    if evaluation_context not in EVALUATION_CONTEXTS:
        raise EvaluationError(
            f"Question {identifier!r}: evaluation_context must be 'standard' or 'internal'"
        )

    source_match = raw_question.get("source_match", "any")
    if source_match not in SOURCE_MATCH_MODES:
        raise EvaluationError(
            f"Question {identifier!r}: source_match must be one of: "
            + ", ".join(sorted(SOURCE_MATCH_MODES))
        )

    minimum_expected_sources = raw_question.get("minimum_expected_sources")
    if minimum_expected_sources is not None:
        if isinstance(minimum_expected_sources, bool) or not isinstance(minimum_expected_sources, int):
            raise EvaluationError(
                f"Question {identifier!r}: minimum_expected_sources must be an integer"
            )

    notes = raw_question.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise EvaluationError(f"Question {identifier!r}: notes must be a string")

    expected_sources = string_tuple(
        raw_question.get("expected_sources", []), "expected_sources", identifier
    )
    if not should_abstain and not expected_sources:
        raise EvaluationError(
            f"Question {identifier!r} must define expected_sources unless should_abstain is true"
        )
    if should_abstain and expected_sources:
        raise EvaluationError(
            f"Question {identifier!r}: should_abstain questions must have no expected_sources"
        )
    if source_match == "all" and len(expected_sources) < 2:
        raise EvaluationError(
            f"Question {identifier!r}: source_match='all' requires at least two sources"
        )
    if source_match == "at_least":
        if minimum_expected_sources is None:
            raise EvaluationError(
                f"Question {identifier!r}: minimum_expected_sources is required for source_match='at_least'"
            )
        if not 1 <= minimum_expected_sources <= len(expected_sources):
            raise EvaluationError(
                f"Question {identifier!r}: minimum_expected_sources is outside the expected_sources range"
            )
    elif minimum_expected_sources is not None:
        raise EvaluationError(
            f"Question {identifier!r}: minimum_expected_sources is only valid for source_match='at_least'"
        )

    maximum_top_score = optional_float(
        raw_question.get("maximum_top_score"), "maximum_top_score", identifier
    )
    if maximum_top_score is not None and not should_abstain:
        raise EvaluationError(
            f"Question {identifier!r}: maximum_top_score is only valid when should_abstain is true"
        )

    return EvaluationQuestion(
        identifier=identifier,
        question=question.strip(),
        category=category.strip(),
        enabled=enabled,
        expected_sources=expected_sources,
        expected_terms=string_tuple(raw_question.get("expected_terms", []), "expected_terms", identifier),
        forbidden_terms=string_tuple(raw_question.get("forbidden_terms", []), "forbidden_terms", identifier),
        expected_heading_terms=string_tuple(
            raw_question.get("expected_heading_terms", []), "expected_heading_terms", identifier
        ),
        should_abstain=should_abstain,
        expected_access_level=expected_access_level,
        evaluation_context=evaluation_context,
        source_match=source_match,
        minimum_expected_sources=minimum_expected_sources,
        maximum_top_score=maximum_top_score,
        notes=notes.strip() if notes else None,
    )


def load_evaluation_questions(path: Path) -> list[EvaluationQuestion]:
    """Load and validate evaluation questions from YAML."""
    if not path.exists():
        raise EvaluationError(f"Evaluation question file does not exist: {path}")
    if not path.is_file():
        raise EvaluationError(f"Evaluation question path is not a file: {path}")
    try:
        raw_document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise EvaluationError(f"Could not load evaluation questions: {path}") from exc
    if not isinstance(raw_document, dict):
        raise EvaluationError("Evaluation YAML root must be a mapping")
    raw_questions = raw_document.get("questions")
    if not isinstance(raw_questions, list):
        raise EvaluationError("Evaluation YAML must contain a questions list")

    questions = [
        parse_evaluation_question(raw, number)
        for number, raw in enumerate(raw_questions, start=1)
    ]
    identifiers = [question.identifier for question in questions]
    duplicates = sorted(
        identifier for identifier, count in Counter(identifiers).items() if count > 1
    )
    if duplicates:
        raise EvaluationError("Duplicate evaluation question IDs: " + ", ".join(duplicates))
    return questions


def contains_all_terms(text: str, terms: tuple[str, ...]) -> bool:
    normalized_text = text.casefold()
    return all(term.casefold() in normalized_text for term in terms)


def find_matching_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    normalized_text = text.casefold()
    return tuple(term for term in terms if term.casefold() in normalized_text)


def matched_source_count(
    results: list[SearchResult], expected_sources: tuple[str, ...], cutoff: int
) -> int:
    returned = {result.source_path for result in results[:cutoff]}
    return len(set(expected_sources) & returned)


def any_source_hit(
    results: list[SearchResult], expected_sources: tuple[str, ...], cutoff: int
) -> bool:
    return matched_source_count(results, expected_sources, cutoff) > 0


def source_hit(
    results: list[SearchResult],
    expected_sources: tuple[str, ...],
    cutoff: int,
) -> bool:
    """
    Determine whether any expected source occurs before a rank cutoff.

    This compatibility wrapper preserves the original evaluator API.
    New code should use ``any_source_hit`` for explicit any-match
    semantics or ``source_requirement_hit`` for configurable source
    requirements.
    """
    return any_source_hit(
        results=results,
        expected_sources=expected_sources,
        cutoff=cutoff,
    )

def source_requirement_hit(
    results: list[SearchResult],
    expected_sources: tuple[str, ...],
    cutoff: int,
    source_match: str,
    minimum_expected_sources: int | None,
) -> bool:
    matched = matched_source_count(results, expected_sources, cutoff)
    if source_match == "any":
        return matched >= 1
    if source_match == "all":
        return matched == len(set(expected_sources))
    if source_match == "at_least":
        if minimum_expected_sources is None:
            raise EvaluationError("minimum_expected_sources is required")
        return matched >= minimum_expected_sources
    raise EvaluationError(f"Unsupported source_match value: {source_match}")


def serialize_results(results: list[SearchResult]) -> list[RankedResult]:
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


def combined_result_text(results: list[SearchResult]) -> str:
    return "\n".join(
        result.document_title + "\n" + result.heading_path + "\n" + result.content
        for result in results
    )


def evaluate_question(
    question: EvaluationQuestion,
    top_k: int,
    retriever: Retriever = retrieve_chunks,
    default_abstention_max_score: float | None = None,
) -> QuestionEvaluation:
    """Evaluate retrieval for one question."""
    evaluation = QuestionEvaluation(
        identifier=question.identifier,
        question=question.question,
        category=question.category,
        expected_sources=question.expected_sources,
        expected_terms=question.expected_terms,
        forbidden_terms=question.forbidden_terms,
        expected_heading_terms=question.expected_heading_terms,
        should_abstain=question.should_abstain,
        expected_access_level=question.expected_access_level,
        evaluation_context=question.evaluation_context,
        source_match=question.source_match,
        minimum_expected_sources=question.minimum_expected_sources,
        maximum_top_score=question.maximum_top_score,
    )

    try:
        standard_results = retriever(
            query=question.question, limit=top_k, include_internal=False
        )
        evaluation.internal_leakage = any(
            result.access_level == "internal"
            or result.source_path.startswith("internal_only/")
            for result in standard_results
        )

        if question.evaluation_context == "internal":
            results = retriever(
                query=question.question, limit=top_k, include_internal=True
            )
            evaluation.internal_access_hit = any_source_hit(
                results, question.expected_sources, top_k
            )
        else:
            results = standard_results

        evaluation.results = serialize_results(results)
        if results:
            evaluation.top_score = results[0].score

        if question.should_abstain:
            threshold = (
                question.maximum_top_score
                if question.maximum_top_score is not None
                else default_abstention_max_score
            )
            if threshold is not None:
                evaluation.maximum_top_score = threshold
                evaluation.abstention_pass = (
                    evaluation.top_score is None or evaluation.top_score < threshold
                )
        else:
            for cutoff, hit_field, requirement_field in (
                (1, "source_hit_at_1", "source_requirement_at_1"),
                (min(3, top_k), "source_hit_at_3", "source_requirement_at_3"),
                (min(5, top_k), "source_hit_at_5", "source_requirement_at_5"),
            ):
                setattr(
                    evaluation,
                    hit_field,
                    any_source_hit(results, question.expected_sources, cutoff),
                )
                setattr(
                    evaluation,
                    requirement_field,
                    source_requirement_hit(
                        results,
                        question.expected_sources,
                        cutoff,
                        question.source_match,
                        question.minimum_expected_sources,
                    ),
                )

        # Bind expected content and headings to expected-source chunks whenever possible.
        expected_source_results = [
            result for result in results if result.source_path in question.expected_sources
        ]
        scoring_results = expected_source_results if expected_source_results else results

        if question.expected_heading_terms:
            headings = "\n".join(result.heading_path for result in scoring_results)
            evaluation.heading_terms_hit = contains_all_terms(
                headings, question.expected_heading_terms
            )

        complete_retrieved_text = combined_result_text(results)
        if question.expected_terms:
            evaluation.content_terms_hit = contains_all_terms(
                combined_result_text(scoring_results), question.expected_terms
            )
        if question.forbidden_terms:
            matched = find_matching_terms(complete_retrieved_text, question.forbidden_terms)
            evaluation.matched_forbidden_terms = matched
            evaluation.forbidden_terms_absent = not matched

    except Exception as exc:
        LOGGER.exception("Evaluation failed for question %s", question.identifier)
        evaluation.error = str(exc)

    return evaluation


def safe_rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def evaluate_questions(
    questions: list[EvaluationQuestion],
    evaluation_path: Path,
    top_k: int = 5,
    category: str | None = None,
    retriever: Retriever = retrieve_chunks,
    default_abstention_max_score: float | None = None,
) -> EvaluationSummary:
    """Evaluate all enabled questions."""
    if top_k < 5:
        raise EvaluationError("Evaluation top_k must be at least 5 to calculate Hit@5")
    if default_abstention_max_score is not None and not 0 <= default_abstention_max_score <= 1:
        raise EvaluationError("default_abstention_max_score must be between 0 and 1")

    selected = [
        q for q in questions
        if q.enabled and (category is None or q.category == category)
    ]
    if not selected:
        raise EvaluationError("No enabled evaluation questions matched the filters")

    started = datetime.now(timezone.utc)
    results = [
        evaluate_question(q, top_k, retriever, default_abstention_max_score)
        for q in selected
    ]
    completed = datetime.now(timezone.utc)

    answerable = [r for r in results if not r.should_abstain and r.error is None]
    no_answer = [r for r in results if r.should_abstain and r.error is None]
    headings = [r for r in results if r.heading_terms_hit is not None and r.error is None]
    terms = [r for r in results if r.content_terms_hit is not None and r.error is None]
    forbidden = [r for r in results if r.forbidden_terms_absent is not None and r.error is None]
    internal = [r for r in results if r.evaluation_context == "internal" and r.error is None]
    abstentions = [r for r in no_answer if r.abstention_pass is not None]
    no_answer_scores = [r.top_score for r in no_answer if r.top_score is not None]

    def count_true(attribute: str, rows: list[QuestionEvaluation]) -> int:
        return sum(getattr(row, attribute) is True for row in rows)

    hit1, hit3, hit5 = (
        count_true("source_hit_at_1", answerable),
        count_true("source_hit_at_3", answerable),
        count_true("source_hit_at_5", answerable),
    )
    req1, req3, req5 = (
        count_true("source_requirement_at_1", answerable),
        count_true("source_requirement_at_3", answerable),
        count_true("source_requirement_at_5", answerable),
    )
    heading_hits = count_true("heading_terms_hit", headings)
    term_hits = count_true("content_terms_hit", terms)
    forbidden_absent = count_true("forbidden_terms_absent", forbidden)
    internal_hits = count_true("internal_access_hit", internal)
    abstention_hits = count_true("abstention_pass", abstentions)

    return EvaluationSummary(
        evaluation_path=str(evaluation_path), top_k=top_k, category_filter=category,
        started_at=started.isoformat(), completed_at=completed.isoformat(),
        questions_loaded=len(questions), questions_evaluated=len(results),
        answerable_questions=len(answerable), no_answer_questions=len(no_answer),
        internal_questions=len(internal), errors=sum(r.error is not None for r in results),
        source_hit_at_1_count=hit1, source_hit_at_3_count=hit3, source_hit_at_5_count=hit5,
        source_hit_at_1_rate=safe_rate(hit1, len(answerable)),
        source_hit_at_3_rate=safe_rate(hit3, len(answerable)),
        source_hit_at_5_rate=safe_rate(hit5, len(answerable)),
        source_requirement_at_1_count=req1,
        source_requirement_at_3_count=req3,
        source_requirement_at_5_count=req5,
        source_requirement_at_1_rate=safe_rate(req1, len(answerable)),
        source_requirement_at_3_rate=safe_rate(req3, len(answerable)),
        source_requirement_at_5_rate=safe_rate(req5, len(answerable)),
        heading_questions=len(headings), heading_hit_count=heading_hits,
        heading_hit_rate=safe_rate(heading_hits, len(headings)),
        term_questions=len(terms), term_hit_count=term_hits,
        term_hit_rate=safe_rate(term_hits, len(terms)),
        forbidden_term_questions=len(forbidden),
        forbidden_terms_absent_count=forbidden_absent,
        forbidden_terms_absent_rate=safe_rate(forbidden_absent, len(forbidden)),
        internal_leakage_count=sum(r.internal_leakage for r in results),
        internal_access_hit_count=internal_hits,
        internal_access_hit_rate=safe_rate(internal_hits, len(internal)),
        abstention_questions_scored=len(abstentions), abstention_pass_count=abstention_hits,
        abstention_pass_rate=safe_rate(abstention_hits, len(abstentions)),
        no_answer_mean_top_score=(statistics.mean(no_answer_scores) if no_answer_scores else None),
        no_answer_max_top_score=(max(no_answer_scores) if no_answer_scores else None),
        category_counts=dict(sorted(Counter(r.category for r in results).items())),
        results=results,
    )


def percentage(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def print_evaluation_summary(summary: EvaluationSummary, show_failures: bool = True) -> None:
    """Print an evaluation report."""
    print("Retrieval evaluation")
    print("====================")
    print(f"Questions loaded: {summary.questions_loaded}")
    print(f"Questions evaluated: {summary.questions_evaluated}")
    print(f"Answerable questions: {summary.answerable_questions}")
    print(f"No-answer questions: {summary.no_answer_questions}")
    print(f"Internal questions: {summary.internal_questions}")
    print(f"Errors: {summary.errors}")

    print("\nAny expected source retrieval:")
    for label, count, rate in (
        ("Hit@1", summary.source_hit_at_1_count, summary.source_hit_at_1_rate),
        ("Hit@3", summary.source_hit_at_3_count, summary.source_hit_at_3_rate),
        ("Hit@5", summary.source_hit_at_5_count, summary.source_hit_at_5_rate),
    ):
        print(f"- {label}: {count}/{summary.answerable_questions} ({percentage(rate)})")

    print("\nPer-question source requirement:")
    for label, count, rate in (
        ("Pass@1", summary.source_requirement_at_1_count, summary.source_requirement_at_1_rate),
        ("Pass@3", summary.source_requirement_at_3_count, summary.source_requirement_at_3_rate),
        ("Pass@5", summary.source_requirement_at_5_count, summary.source_requirement_at_5_rate),
    ):
        print(f"- {label}: {count}/{summary.answerable_questions} ({percentage(rate)})")

    print("\nExpected context:")
    print(f"- Heading terms: {summary.heading_hit_count}/{summary.heading_questions} ({percentage(summary.heading_hit_rate)})")
    print(f"- Content terms: {summary.term_hit_count}/{summary.term_questions} ({percentage(summary.term_hit_rate)})")
    print(f"- Forbidden terms absent: {summary.forbidden_terms_absent_count}/{summary.forbidden_term_questions} ({percentage(summary.forbidden_terms_absent_rate)})")

    print("\nAccess control:")
    print(f"- Standard searches leaking internal chunks: {summary.internal_leakage_count}")
    print(f"- Internal retrieval: {summary.internal_access_hit_count}/{summary.internal_questions} ({percentage(summary.internal_access_hit_rate)})")

    print("\nAbstention:")
    print(f"- Scored abstentions: {summary.abstention_pass_count}/{summary.abstention_questions_scored} ({percentage(summary.abstention_pass_rate)})")
    print("- Mean top score: " + (f"{summary.no_answer_mean_top_score:.4f}" if summary.no_answer_mean_top_score is not None else "n/a"))
    print("- Maximum top score: " + (f"{summary.no_answer_max_top_score:.4f}" if summary.no_answer_max_top_score is not None else "n/a"))

    print("\nCategories:")
    for category, count in summary.category_counts.items():
        print(f"- {category}: {count}")

    if not show_failures:
        return

    failure_groups = (
        ("Failed any-source Hit@5", lambda r: r.failed_source_at_5),
        ("Failed source requirement@5", lambda r: r.failed_source_requirement_at_5),
        ("Failed content terms", lambda r: r.failed_content_terms),
        ("Failed heading terms", lambda r: r.failed_heading_terms),
        ("Forbidden terms found", lambda r: r.failed_forbidden_terms),
        ("Failed internal access", lambda r: r.failed_internal_access),
        ("Failed abstention", lambda r: r.failed_abstention),
    )
    for heading, predicate in failure_groups:
        failed = [r for r in summary.results if r.error is None and predicate(r)]
        if not failed:
            continue
        print(f"\n{heading}:")
        for result in failed:
            print(f"\n- {result.identifier}")
            print(f"  Question: {result.question}")
            if result.expected_sources:
                print("  Expected: " + ", ".join(result.expected_sources))
            if result.matched_forbidden_terms:
                print("  Matched forbidden terms: " + ", ".join(result.matched_forbidden_terms))
            print("  Returned:")
            for retrieved in result.results:
                print(f"    {retrieved.rank}. {retrieved.source_path} ({retrieved.score:.4f})")
                print(f"       {retrieved.heading_path}")

    errors = [r for r in summary.results if r.error is not None]
    if errors:
        print("\nEvaluation errors:")
        for result in errors:
            print(f"- {result.identifier}: {result.error}")


def write_json_report(summary: EvaluationSummary, path: Path) -> None:
    """Write a complete evaluation report as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
