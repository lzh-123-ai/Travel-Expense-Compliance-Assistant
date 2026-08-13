from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Literal

from app.evaluation.contracts import EvalCase

AnswerStatus = Literal["answered", "refused", "needs_clarification", "provider_error"]


@dataclass(frozen=True)
class AnswerCaseMetric:
    case_id: str
    expected_status: str
    actual_status: str
    status_correct: bool
    expected_version_labels: tuple[str, ...]
    forbidden_version_labels: tuple[str, ...]
    cited_version_labels: tuple[str, ...]
    missing_expected_labels: tuple[str, ...]
    cited_forbidden_labels: tuple[str, ...]
    unexpected_citation_labels: tuple[str, ...]
    citation_precision: float | None
    citation_recall: float | None
    forbidden_source_safe: bool
    hard_passed: bool
    answer_point_review: Literal["pending_human_review"] = "pending_human_review"


@dataclass(frozen=True)
class AnswerAggregateMetrics:
    evaluated_cases: int
    answered_cases: int
    refusal_cases: int
    clarification_cases: int
    status_accuracy: float
    answered_status_accuracy: float
    refusal_status_accuracy: float
    clarification_status_accuracy: float
    citation_precision: float
    citation_recall: float
    forbidden_source_safety: float
    hard_pass_rate: float
    answer_point_review_status: Literal["pending_human_review"] = "pending_human_review"


def expected_answer_status(case: EvalCase) -> str:
    if case.clarification_required:
        return "needs_clarification"
    if case.should_refuse:
        return "refused"
    return "answered"


def score_answer_case(
    case: EvalCase,
    *,
    actual_status: AnswerStatus,
    cited_version_labels: list[str],
) -> AnswerCaseMetric:
    expected_status = expected_answer_status(case)
    expected = tuple(
        dict.fromkeys(
            source.version_label for source in case.expected_sources if source.retrieval_allowed
        )
    )
    forbidden = tuple(
        dict.fromkeys(
            source.version_label for source in case.expected_sources if not source.retrieval_allowed
        )
    )
    cited = tuple(dict.fromkeys(label for label in cited_version_labels if label))
    cited_set = set(cited)
    expected_set = set(expected)
    forbidden_set = set(forbidden)
    missing = tuple(label for label in expected if label not in cited_set)
    cited_forbidden = tuple(label for label in forbidden if label in cited_set)
    unexpected = tuple(
        label for label in cited if label not in expected_set and label not in forbidden_set
    )
    citation_precision = len(cited_set & expected_set) / len(cited_set) if cited_set else None
    citation_recall = len(cited_set & expected_set) / len(expected_set) if expected_set else None
    status_correct = actual_status == expected_status
    forbidden_source_safe = not cited_forbidden
    citation_boundary_passed = (
        not missing and not unexpected if expected_status == "answered" else True
    )
    return AnswerCaseMetric(
        case_id=case.id,
        expected_status=expected_status,
        actual_status=actual_status,
        status_correct=status_correct,
        expected_version_labels=expected,
        forbidden_version_labels=forbidden,
        cited_version_labels=cited,
        missing_expected_labels=missing,
        cited_forbidden_labels=cited_forbidden,
        unexpected_citation_labels=unexpected,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        forbidden_source_safe=forbidden_source_safe,
        hard_passed=status_correct and forbidden_source_safe and citation_boundary_passed,
    )


def aggregate_answer_metrics(cases: list[AnswerCaseMetric]) -> AnswerAggregateMetrics:
    if not cases:
        raise ValueError("At least one answer case is required")
    answered = [case for case in cases if case.expected_status == "answered"]
    refusals = [case for case in cases if case.expected_status == "refused"]
    clarifications = [case for case in cases if case.expected_status == "needs_clarification"]
    cited = [case for case in cases if case.citation_precision is not None]
    recall = [case for case in answered if case.citation_recall is not None]
    forbidden = [case for case in cases if case.forbidden_version_labels]
    return AnswerAggregateMetrics(
        evaluated_cases=len(cases),
        answered_cases=len(answered),
        refusal_cases=len(refusals),
        clarification_cases=len(clarifications),
        status_accuracy=fmean(case.status_correct for case in cases),
        answered_status_accuracy=_accuracy(answered),
        refusal_status_accuracy=_accuracy(refusals),
        clarification_status_accuracy=_accuracy(clarifications),
        citation_precision=(
            fmean(case.citation_precision for case in cited if case.citation_precision is not None)
            if cited
            else 1.0
        ),
        citation_recall=(
            fmean(case.citation_recall for case in recall if case.citation_recall is not None)
            if recall
            else 1.0
        ),
        forbidden_source_safety=(
            fmean(case.forbidden_source_safe for case in forbidden) if forbidden else 1.0
        ),
        hard_pass_rate=fmean(case.hard_passed for case in cases),
    )


def _accuracy(cases: list[AnswerCaseMetric]) -> float:
    return fmean(case.status_correct for case in cases) if cases else 1.0
