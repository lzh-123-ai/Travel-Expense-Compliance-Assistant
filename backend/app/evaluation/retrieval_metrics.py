"""确定性的检索质量与安全边界指标。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from app.evaluation.contracts import RetrievalEvalCase


@dataclass(frozen=True)
class RetrievalCaseMetric:
    case_id: str
    category: str
    top_k: int
    expected_version_labels: tuple[str, ...]
    forbidden_version_labels: tuple[str, ...]
    retrieved_version_labels: tuple[str, ...]
    missing_expected_labels: tuple[str, ...]
    retrieved_forbidden_labels: tuple[str, ...]
    recall_at_k: float | None
    reciprocal_rank: float | None
    version_correct: bool | None
    passed: bool


@dataclass(frozen=True)
class RetrievalAggregateMetrics:
    evaluated_cases: int
    cases_with_expected_labels: int
    cases_with_forbidden_labels: int
    macro_recall_at_5: float
    mean_reciprocal_rank: float
    full_expected_set_accuracy: float
    forbidden_filter_accuracy: float
    version_correctness: float
    case_pass_rate: float


def score_retrieval_case(
    case: RetrievalEvalCase,
    retrieved_version_labels: list[str],
) -> RetrievalCaseMetric:
    unique_retrieved = tuple(dict.fromkeys(retrieved_version_labels))
    retrieved = set(unique_retrieved)
    missing = tuple(label for label in case.expected_version_labels if label not in retrieved)
    forbidden = tuple(label for label in case.forbidden_version_labels if label in retrieved)
    recall = (
        1.0 - (len(missing) / len(case.expected_version_labels))
        if case.expected_version_labels
        else None
    )
    first_relevant_rank = next(
        (
            rank
            for rank, label in enumerate(unique_retrieved, start=1)
            if label in case.expected_version_labels
        ),
        None,
    )
    reciprocal_rank = (
        1.0 / first_relevant_rank
        if first_relevant_rank is not None
        else (0.0 if case.expected_version_labels else None)
    )
    passed = not missing and not forbidden
    version_correct = passed if case.category in {"version_filter", "date_filter"} else None
    return RetrievalCaseMetric(
        case_id=case.id,
        category=case.category,
        top_k=case.top_k,
        expected_version_labels=tuple(case.expected_version_labels),
        forbidden_version_labels=tuple(case.forbidden_version_labels),
        retrieved_version_labels=unique_retrieved,
        missing_expected_labels=missing,
        retrieved_forbidden_labels=forbidden,
        recall_at_k=recall,
        reciprocal_rank=reciprocal_rank,
        version_correct=version_correct,
        passed=passed,
    )


def aggregate_retrieval_metrics(
    cases: list[RetrievalCaseMetric],
) -> RetrievalAggregateMetrics:
    if not cases:
        raise ValueError("At least one retrieval case is required")
    expected_cases = [case for case in cases if case.recall_at_k is not None]
    forbidden_cases = [case for case in cases if case.forbidden_version_labels]
    version_cases = [case for case in cases if case.version_correct is not None]
    if not expected_cases:
        raise ValueError("At least one case with expected labels is required")

    return RetrievalAggregateMetrics(
        evaluated_cases=len(cases),
        cases_with_expected_labels=len(expected_cases),
        cases_with_forbidden_labels=len(forbidden_cases),
        macro_recall_at_5=fmean(case.recall_at_k or 0.0 for case in expected_cases),
        mean_reciprocal_rank=fmean(case.reciprocal_rank or 0.0 for case in expected_cases),
        full_expected_set_accuracy=fmean(
            not case.missing_expected_labels for case in expected_cases
        ),
        forbidden_filter_accuracy=(
            fmean(not case.retrieved_forbidden_labels for case in forbidden_cases)
            if forbidden_cases
            else 1.0
        ),
        version_correctness=(
            fmean(case.version_correct for case in version_cases) if version_cases else 1.0
        ),
        case_pass_rate=fmean(case.passed for case in cases),
    )
