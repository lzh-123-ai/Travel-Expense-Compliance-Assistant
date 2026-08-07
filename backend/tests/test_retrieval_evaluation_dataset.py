import json
from pathlib import Path

from app.evaluation import load_retrieval_eval_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage9_retrieval_v0.json"
MANIFEST_PATH = PROJECT_ROOT / "data" / "policies" / "manifest.json"
BASELINE_PATH = PROJECT_ROOT / "data" / "evaluation" / "results" / "stage9_bge_dense_baseline.json"


def test_stage9_retrieval_dataset_has_thirty_unique_reviewed_cases() -> None:
    dataset = load_retrieval_eval_dataset(DATASET_PATH)
    assert dataset.dataset_version == "stage9-retrieval-v0"
    assert dataset.annotation_status == "reviewed"
    assert len(dataset.cases) == 30
    assert len({case.id for case in dataset.cases}) == 30
    assert {case.category for case in dataset.cases} == {
        "direct_fact",
        "cross_document",
        "version_filter",
        "date_filter",
        "permission_filter",
        "scope_boundary",
    }


def test_retrieval_labels_match_manifest_versions() -> None:
    dataset = load_retrieval_eval_dataset(DATASET_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    known_labels = {document["version_label"] for document in manifest["documents"]}
    for case in dataset.cases:
        assert set(case.expected_version_labels) <= known_labels
        assert set(case.forbidden_version_labels) <= known_labels


def test_retrieval_dataset_freezes_version_date_and_permission_boundaries() -> None:
    cases = {case.id: case for case in load_retrieval_eval_dataset(DATASET_PATH).cases}
    assert cases["DENSE-EVAL-001"].expected_version_labels == ["TRAVEL-2025.1"]
    assert cases["DENSE-EVAL-002"].expected_version_labels == ["TRAVEL-2026.1"]
    assert "HOTEL-SUP-2026.1" in cases["DENSE-EVAL-021"].forbidden_version_labels
    assert cases["DENSE-EVAL-024"].allowed_scopes == ["all_employees"]
    assert "AUDIT-2026.1" in cases["DENSE-EVAL-024"].forbidden_version_labels
    assert "finance_only" in cases["DENSE-EVAL-025"].allowed_scopes


def test_stage9_baseline_covers_reviewed_cases_and_meets_acceptance_thresholds() -> None:
    dataset = load_retrieval_eval_dataset(DATASET_PATH)
    report = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    assert report["dataset_version"] == dataset.dataset_version
    assert report["dataset_annotation_status"] == "reviewed"
    assert report["model_name"] == "BAAI/bge-small-zh-v1.5"
    assert (
        report["model_weight_sha256"]
        == "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026"
    )
    assert {case["case_id"] for case in report["cases"]} == {case.id for case in dataset.cases}
    assert report["aggregate"]["macro_recall_at_5"] >= 0.9
    assert report["aggregate"]["full_expected_set_accuracy"] >= 0.9
    assert report["aggregate"]["forbidden_filter_accuracy"] == 1.0
