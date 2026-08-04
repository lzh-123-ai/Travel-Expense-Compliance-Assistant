import json
from datetime import UTC, datetime
from pathlib import Path

from app.evaluation import (
    EvalCaseResult,
    EvaluationRun,
    EvaluationRunConfig,
    load_eval_dataset,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage8_v0.json"
MANIFEST_PATH = PROJECT_ROOT / "data" / "policies" / "manifest.json"


def test_stage8_dataset_has_twenty_unique_human_reviewed_cases() -> None:
    dataset = load_eval_dataset(DATASET_PATH)

    assert dataset.dataset_version == "stage8-v0"
    assert dataset.annotation_status == "reviewed"
    assert len(dataset.cases) == 20
    assert len({case.id for case in dataset.cases}) == 20
    assert {case.category for case in dataset.cases} == {
        "direct_fact",
        "multi_condition",
        "cross_document",
        "version_conflict",
        "no_answer",
        "permission",
    }


def test_every_expected_source_matches_a_versioned_manifest_document() -> None:
    dataset = load_eval_dataset(DATASET_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    documents = {item["document_key"]: item for item in manifest["documents"]}

    for case in dataset.cases:
        for source in case.expected_sources:
            manifest_source = documents[source.document_key]
            assert source.version_label == manifest_source["version_label"]
            assert (PROJECT_ROOT / "data" / "policies" / manifest_source["relative_path"]).is_file()


def test_dataset_covers_version_no_answer_and_permission_boundaries() -> None:
    dataset = load_eval_dataset(DATASET_PATH)
    cases = {case.id: case for case in dataset.cases}

    assert cases["TRAVEL-EVAL-001"].expected_sources[0].version_label == "TRAVEL-2025.1"
    assert cases["TRAVEL-EVAL-002"].expected_sources[0].version_label == "TRAVEL-2026.1"
    assert cases["TRAVEL-EVAL-017"].clarification_required is True
    assert cases["TRAVEL-EVAL-016"].expected_sources[1].retrieval_allowed is False
    assert cases["TRAVEL-EVAL-018"].should_refuse is True
    assert cases["TRAVEL-EVAL-018"].expected_sources[0].document_key == "domestic_travel_policy_v2"
    assert cases["TRAVEL-EVAL-019"].expected_sources[0].retrieval_allowed is False
    assert cases["TRAVEL-EVAL-019"].execution_allowed is False
    assert cases["TRAVEL-EVAL-020"].expected_sources[0].retrieval_allowed is True


def test_evaluation_run_contract_freezes_reproducibility_and_cost_fields() -> None:
    run = EvaluationRun(
        run_id="run-stage11-example",
        started_at=datetime(2026, 7, 30, tzinfo=UTC),
        config=EvaluationRunConfig(
            dataset_version="stage8-v0",
            retrieval_config_version="hybrid-v1",
            prompt_version="answer-v1",
            model_provider="fixture",
            model_name="deterministic-fixture",
            framework_versions={"app": "0.1.0"},
            temperature=0,
            random_seed=7,
        ),
        results=[
            EvalCaseResult(
                case_id="TRAVEL-EVAL-001",
                retrieved_chunk_ids=["chunk-1"],
                answer="500 元",
                passed=True,
                latency_ms=12,
                input_tokens=30,
                output_tokens=8,
                estimated_cost_usd=0,
            )
        ],
    )

    assert run.config.dataset_version == "stage8-v0"
    assert run.config.retrieval_config_version == "hybrid-v1"
    assert run.results[0].latency_ms == 12
    assert run.results[0].estimated_cost_usd == 0
