from app.evaluation.assistant_benchmark import percentile, summarize_samples


def test_percentile_uses_nearest_rank_for_small_samples() -> None:
    assert percentile([30, 10, 20], 0.5) == 20
    assert percentile([30, 10, 20], 0.95) == 30
    assert percentile([], 0.5) is None


def test_summarize_samples_keeps_errors_and_routes_separate() -> None:
    summary = summarize_samples(
        [
            {"status": "ok", "status_code": 200, "route": "policy_rag", "latency_ms": 10},
            {"status": "ok", "status_code": 200, "route": "reimbursement_status", "latency_ms": 30},
            {"status": "error", "status_code": 503, "route": None, "latency_ms": 50},
        ]
    )

    assert summary["request_count"] == 3
    assert summary["success_count"] == 2
    assert summary["error_count"] == 1
    assert summary["latency_ms"]["p50"] == 30
    assert summary["routes"] == {"policy_rag": 1, "reimbursement_status": 1}
