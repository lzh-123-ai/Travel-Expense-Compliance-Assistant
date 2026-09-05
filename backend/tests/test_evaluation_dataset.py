import json
from datetime import UTC, datetime
from hashlib import file_digest
from pathlib import Path

from app.evaluation import (
    EvalCaseResult,
    EvaluationRun,
    EvaluationRunConfig,
    load_eval_dataset,
)
from app.evaluation.stage11_baseline import _evaluation_purpose

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage8_v0.json"
MANIFEST_PATH = PROJECT_ROOT / "data" / "policies" / "manifest.json"
FINAL_FREEZE_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage16_final_freeze_v0.json"
HOLDOUT_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage16_holdout_v1.json"


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


def test_stage16_freeze_manifest_locks_reviewed_scored_sources() -> None:
    """冻结报告只能汇总已复核且内容指纹未变化的题集。"""

    freeze = json.loads(FINAL_FREEZE_PATH.read_text(encoding="utf-8"))
    assert freeze["freeze_version"] == "stage16-final-freeze-v0"
    assert freeze["annotation_status"] == "reviewed"
    assert freeze["candidate_config"]["answer"]["prompt_version"] == "v1"
    assert freeze["candidate_config"]["retrieval"]["strategy"] == "hybrid+RRF"
    assert freeze["candidate_config"]["retrieval"]["enable_query_rewrite"] is False
    assert freeze["candidate_config"]["retrieval"]["enable_rerank"] is False

    scored_ids: set[str] = set()
    for source in freeze["scored_sources"]:
        path = PROJECT_ROOT / source["path"]
        with path.open("rb") as file:
            assert file_digest(file, "sha256").hexdigest() == source["sha256"]
        dataset = json.loads(path.read_text(encoding="utf-8"))
        ids = {case["id"] for case in dataset["cases"]}
        assert dataset["annotation_status"] == "reviewed"
        assert len(ids) == source["case_count"]
        assert not scored_ids & ids
        scored_ids.update(ids)

    assert len(scored_ids) == freeze["total_scored_cases"] == 72
    diagnostic_paths = {item["path"] for item in freeze["diagnostic_sources"]}
    assert diagnostic_paths == {"data/evaluation/stage11_tuning_v0.json"}


def test_stage16_holdout_is_reviewed_disjoint_and_excluded_from_frozen_score() -> None:
    """确认留出题已人工复核，但仍不得回流为调优材料或冻结集成绩。"""

    holdout = load_eval_dataset(HOLDOUT_PATH)
    freeze = json.loads(FINAL_FREEZE_PATH.read_text(encoding="utf-8"))
    used_questions: set[str] = set()
    for source in [*freeze["scored_sources"], *freeze["diagnostic_sources"]]:
        path = PROJECT_ROOT / source["path"]
        dataset = json.loads(path.read_text(encoding="utf-8"))
        used_questions.update(
            text
            for case in dataset["cases"]
            for text in (case.get("question"), case.get("query"))
            if text
        )

    assert holdout.dataset_version == "stage16-holdout-v1"
    assert holdout.annotation_status == "reviewed"
    assert len(holdout.cases) == 10
    assert {case.question for case in holdout.cases}.isdisjoint(used_questions)
    assert _evaluation_purpose(holdout.dataset_version) == "holdout_validation"
    assert str(HOLDOUT_PATH.relative_to(PROJECT_ROOT)) not in {
        source["path"] for source in freeze["scored_sources"]
    }
