from app.evaluation.contracts import RetrievalEvalCase
from app.evaluation.retrieval_metrics import (
    aggregate_retrieval_metrics,
    score_retrieval_case,
)


def make_case(
    *,
    case_id: str,
    expected: list[str],
    forbidden: list[str] | None = None,
) -> RetrievalEvalCase:
    return RetrievalEvalCase(
        id=case_id,
        category="version_filter",
        query="上海住宿标准",
        expense_date="2026-05-01",
        allowed_scopes=["all_employees"],
        expected_version_labels=expected,
        forbidden_version_labels=forbidden or [],
    )


def test_case_metric_deduplicates_labels_and_detects_missing_and_forbidden() -> None:
    case = make_case(
        case_id="DENSE-EVAL-901",
        expected=["TRAVEL-2026.1", "HOTEL-SUP-2026.1"],
        forbidden=["TRAVEL-2025.1"],
    )

    metric = score_retrieval_case(
        case,
        ["TRAVEL-2026.1", "TRAVEL-2026.1", "TRAVEL-2025.1"],
    )

    assert metric.retrieved_version_labels == ("TRAVEL-2026.1", "TRAVEL-2025.1")
    assert metric.missing_expected_labels == ("HOTEL-SUP-2026.1",)
    assert metric.retrieved_forbidden_labels == ("TRAVEL-2025.1",)
    assert metric.recall_at_k == 0.5
    assert metric.reciprocal_rank == 1.0
    assert metric.version_correct is False
    assert metric.passed is False


def test_aggregate_separates_recall_from_forbidden_filter_accuracy() -> None:
    recalled = score_retrieval_case(
        make_case(case_id="DENSE-EVAL-902", expected=["TRAVEL-2026.1"]),
        ["TRAVEL-2026.1"],
    )
    permission_case = score_retrieval_case(
        make_case(
            case_id="DENSE-EVAL-903",
            expected=[],
            forbidden=["AUDIT-2026.1"],
        ),
        [],
    )

    aggregate = aggregate_retrieval_metrics([recalled, permission_case])

    assert aggregate.macro_recall_at_5 == 1.0
    assert aggregate.mean_reciprocal_rank == 1.0
    assert aggregate.full_expected_set_accuracy == 1.0
    assert aggregate.forbidden_filter_accuracy == 1.0
    assert aggregate.version_correctness == 1.0
    assert aggregate.case_pass_rate == 1.0
