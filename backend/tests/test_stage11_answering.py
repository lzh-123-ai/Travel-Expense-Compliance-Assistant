import hashlib
import json
from collections.abc import AsyncIterator, Callable
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.answers import get_answer_service
from app.db.session import get_db_session
from app.evaluation.answer_metrics import aggregate_answer_metrics, score_answer_case
from app.evaluation.loader import load_eval_dataset
from app.evaluation.stage11_ragas import build_ragas_samples
from app.main import app
from app.services.answer_prompts import build_answer_evidence, build_answer_prompt
from app.services.answering import (
    AnswerCitation,
    AnswerDraft,
    AnswerOutputError,
    AnswerResult,
    AnswerService,
    GeneratedAnswer,
    GenerationUsage,
)
from app.services.bailian_answer_provider import (
    AnswerProviderError,
    BailianAnswerProvider,
    get_answer_provider,
)
from app.services.embeddings import get_embedding_provider
from app.services.retrieval import (
    PUBLIC_DOCUMENT_SCOPES,
    HybridRetrievalService,
    HybridSearchHit,
    HybridSearchResult,
)

KB_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
CHUNK_ID = UUID("1056bc33-2194-4965-b148-9bde6180db44")
STAGE11_DATASET_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "evaluation" / "stage8_v0.json"
)
STAGE11_TUNING_DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "evaluation"
    / "stage11_tuning_v0.json"
)
STAGE11_HUMAN_REVIEW_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "evaluation"
    / "results"
    / "stage11_answer_point_review.json"
)
STAGE11_TUNING_REVIEW_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "evaluation"
    / "results"
    / "stage11_tuning_answer_point_review.json"
)
STAGE11_RESULTS_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "evaluation" / "results"
)
STAGE11_RAGAS_SUMMARY_PATH = STAGE11_RESULTS_DIR / "stage11_ragas_v1_summary.json"
POLICY_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "policies" / "manifest.json"
)


class FakeEmbeddingProvider:
    provider_name = "fixture"
    model_name = "fixture-embedding"
    dimension = 512


class FakeAnswerProvider:
    provider_name = "fixture"
    model_name = "fixture-answer"

    def __init__(self, generated: GeneratedAnswer) -> None:
        self.generated = generated
        self.prompts = []

    async def generate_answer(self, prompt):
        self.prompts.append(prompt)
        return self.generated


def make_hit(*, partial: bool = False) -> HybridSearchHit:
    return HybridSearchHit(
        chunk_id=CHUNK_ID,
        document_id=DOCUMENT_ID,
        original_filename="travel-policy.md",
        version_label="TRAVEL-2026.1",
        content="上海住宿费普通标准为每人每晚六百元。",
        page_start=None,
        page_end=None,
        section_path=("第四章 住宿费",),
        dense_similarity=0.9,
        keyword_score=0.8,
        dense_rank=1,
        keyword_rank=1,
        rrf_score=2 / 61,
        policy_type="travel_policy",
        effective_from=date(2026, 1, 1),
        text_extraction_status="partial" if partial else "complete",
        needs_ocr=partial,
    )


def make_provider(*, citation_ids: list[str]) -> FakeAnswerProvider:
    return FakeAnswerProvider(
        GeneratedAnswer(
            draft=AnswerDraft(
                status="answered",
                answer="普通情况下每人每晚最高六百元。",
                citation_ids=citation_ids,
            ),
            usage=GenerationUsage(input_tokens=100, output_tokens=20),
        )
    )


def test_v1_prompt_marks_context_untrusted_and_serializes_traceable_sources() -> None:
    evidence = build_answer_evidence((make_hit(),))

    prompt = build_answer_prompt(
        version="v1",
        question="上海住宿费是多少？",
        expense_date=date(2026, 5, 1),
        evidence=evidence,
        version_conflicts=(),
    )

    assert "不可信数据" in prompt.system
    assert "只能依据" in prompt.system
    assert "‘不可以’或‘不符合’" in prompt.system
    assert prompt.revision == "v1-structured-r2"
    assert '"answered": "现有证据足以回答' in prompt.user
    assert '"source_id": "S1"' in prompt.user
    assert '"version_label": "TRAVEL-2026.1"' in prompt.user


def test_v2_prompt_adds_failure_driven_examples_without_changing_evidence() -> None:
    evidence = build_answer_evidence((make_hit(),))

    prompt = build_answer_prompt(
        version="v2",
        question="上海住宿费是多少？",
        expense_date=date(2026, 5, 1),
        evidence=evidence,
        version_conflicts=(),
    )

    assert prompt.revision == "v2-few-shot-r1"
    assert "应合并两者并引用两份来源" in prompt.system
    assert "不主动套用或引用该例外" in prompt.system
    assert "不要重复追问同一条件" in prompt.system
    assert "直接回答相对期限" in prompt.system
    assert '"source_id": "S1"' in prompt.user


def test_stage11_tuning_dataset_is_reviewed_and_independent_from_frozen_cases() -> None:
    from app.evaluation.stage11_baseline import _evaluation_purpose

    frozen = load_eval_dataset(STAGE11_DATASET_PATH)
    tuning = load_eval_dataset(STAGE11_TUNING_DATASET_PATH)

    frozen_questions = {case.question for case in frozen.cases}
    tuning_questions = {case.question for case in tuning.cases}

    assert tuning.dataset_version == "stage11-tuning-v0"
    assert tuning.annotation_status == "reviewed"
    assert _evaluation_purpose(tuning.dataset_version) == "prompt_tuning"
    assert _evaluation_purpose(frozen.dataset_version) == "frozen_regression"
    assert len(tuning.cases) == 8
    assert all(case.id.startswith("TRAVEL-TUNE-") for case in tuning.cases)
    assert frozen_questions.isdisjoint(tuning_questions)
    assert all(
        source.retrieval_allowed
        for case in tuning.cases
        for source in case.expected_sources
    )
    assert {
        label
        for case in tuning.cases
        for label in case.failure_labels
    } >= {
        "missing_document",
        "irrelevant_exception",
        "over_clarification",
        "wrong_deadline",
    }


def test_stage11_tuning_sources_match_manifest_dates_scopes_and_sections() -> None:
    tuning = load_eval_dataset(STAGE11_TUNING_DATASET_PATH)
    manifest = json.loads(POLICY_MANIFEST_PATH.read_text(encoding="utf-8"))
    documents = {item["document_key"]: item for item in manifest["documents"]}
    role_scopes = {
        "employee": {"all_employees"},
        "finance_reviewer": {"all_employees", "finance_only"},
        "policy_admin": {"all_employees", "finance_only"},
    }

    for case in tuning.cases:
        assert case.expense_date is not None
        for source in case.expected_sources:
            document = documents[source.document_key]
            policy_path = POLICY_MANIFEST_PATH.parent / document["relative_path"]
            policy_text = policy_path.read_text(encoding="utf-8")

            assert source.version_label == document["version_label"]
            assert document["access_scope"] in role_scopes[case.role]
            assert date.fromisoformat(document["effective_from"]) <= case.expense_date
            if document["effective_to"] is not None:
                assert case.expense_date <= date.fromisoformat(document["effective_to"])
            assert all(section in policy_text for section in source.relevant_sections)


def test_answer_metrics_require_expected_citations_and_block_forbidden_sources() -> None:
    cases = {case.id: case for case in load_eval_dataset(STAGE11_DATASET_PATH).cases}

    exhibition = score_answer_case(
        cases["TRAVEL-EVAL-015"],
        actual_status="answered",
        cited_version_labels=["TRAVEL-2026.1", "HOTEL-SUP-2026.1"],
    )
    permission_leak = score_answer_case(
        cases["TRAVEL-EVAL-019"],
        actual_status="refused",
        cited_version_labels=["AUDIT-2026.1"],
    )
    aggregate = aggregate_answer_metrics([exhibition, permission_leak])

    assert exhibition.hard_passed is True
    assert permission_leak.status_correct is True
    assert permission_leak.forbidden_source_safe is False
    assert permission_leak.hard_passed is False
    assert aggregate.status_accuracy == 1.0
    assert aggregate.forbidden_source_safety == 0.0


def test_stage11_human_review_covers_dataset_and_matches_summary() -> None:
    dataset_case_ids = {
        case.id for case in load_eval_dataset(STAGE11_DATASET_PATH).cases
    }
    review = json.loads(STAGE11_HUMAN_REVIEW_PATH.read_text(encoding="utf-8"))

    assert review["review_status"] == "confirmed"
    assert review["dataset_cases"] == len(dataset_case_ids) == 20

    for prompt in review["prompts"].values():
        verdicts = prompt["case_verdicts"]
        counts = {
            verdict: sum(value == verdict for value in verdicts.values())
            for verdict in ("full", "partial", "fail")
        }

        assert set(verdicts) == dataset_case_ids
        assert counts["full"] == prompt["full_pass"]
        assert counts["partial"] == prompt["partial_pass"]
        assert counts["fail"] == prompt["fail"]
        assert (
            counts["full"] + 0.5 * counts["partial"]
        ) / len(verdicts) == pytest.approx(prompt["weighted_score"])


def test_stage11_tuning_review_covers_dataset_and_rejects_v2_promotion() -> None:
    dataset_case_ids = {
        case.id for case in load_eval_dataset(STAGE11_TUNING_DATASET_PATH).cases
    }
    review = json.loads(STAGE11_TUNING_REVIEW_PATH.read_text(encoding="utf-8"))

    assert review["review_status"] == "confirmed"
    assert review["dataset_cases"] == len(dataset_case_ids) == 8
    assert review["decision"]["selected_prompt"] == "v1"
    assert review["decision"]["promote_v2"] is False

    for prompt in review["prompts"].values():
        verdicts = prompt["case_verdicts"]
        counts = {
            verdict: sum(value == verdict for value in verdicts.values())
            for verdict in ("full", "partial", "fail")
        }

        assert set(verdicts) == dataset_case_ids
        assert counts["full"] == prompt["full_pass"]
        assert counts["partial"] == prompt["partial_pass"]
        assert counts["fail"] == prompt["fail"]
        assert (
            counts["full"] + 0.5 * counts["partial"]
        ) / len(verdicts) == pytest.approx(prompt["weighted_score"])


def test_stage11_ragas_summary_matches_primary_and_retry_evidence() -> None:
    summary = json.loads(STAGE11_RAGAS_SUMMARY_PATH.read_text(encoding="utf-8"))
    answer_path = STAGE11_RESULTS_DIR / summary["source_answer_report"]["path"]
    primary_path = STAGE11_RESULTS_DIR / summary["ragas_runs"][0]["path"]
    retry_path = STAGE11_RESULTS_DIR / summary["ragas_runs"][1]["path"]

    for path, expected_hash in (
        (answer_path, summary["source_answer_report"]["sha256"]),
        (primary_path, summary["ragas_runs"][0]["sha256"]),
        (retry_path, summary["ragas_runs"][1]["sha256"]),
    ):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash

    primary = json.loads(primary_path.read_text(encoding="utf-8"))["prompts"]["v1"]
    retry = json.loads(retry_path.read_text(encoding="utf-8"))["prompts"]["v1"]
    retry_by_case = {case["case_id"]: case for case in retry["cases"]}
    merged_values = {
        "faithfulness": [],
        "factual_correctness": [],
        "context_recall": [],
    }
    for case in primary["cases"]:
        for metric_name in merged_values:
            value = case["scores"][metric_name]
            if value is None and case["case_id"] in retry_by_case:
                value = retry_by_case[case["case_id"]]["scores"][metric_name]
            if value is not None:
                merged_values[metric_name].append(value)

    assert primary["evaluated_cases"] == summary["scope"]["evaluated_cases"] == 15
    assert summary["merged_metrics"]["metric_slots"] == 45
    assert summary["merged_metrics"]["valid_metric_slots"] == sum(
        len(values) for values in merged_values.values()
    ) == 44
    for metric_name, values in merged_values.items():
        metric_summary = summary["merged_metrics"][metric_name]
        assert metric_summary["valid_cases"] == len(values)
        assert metric_summary["mean"] == pytest.approx(sum(values) / len(values))


def test_ragas_samples_reuse_frozen_answers_without_refusal_cases() -> None:
    report_path = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "evaluation"
        / "results"
        / "stage11_prompt_comparison.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    samples = build_ragas_samples(
        report,
        prompt_names=("v0",),
        case_ids=frozenset({"TRAVEL-EVAL-001", "TRAVEL-EVAL-019"}),
    )

    assert [sample["case_id"] for sample in samples["v0"]] == ["TRAVEL-EVAL-001"]
    assert samples["v0"][0]["retrieved_contexts"]
    assert "500 元" in samples["v0"][0]["reference"]


@pytest.mark.asyncio
async def test_missing_expense_date_clarifies_without_retrieval_or_model_call() -> None:
    retriever = AsyncMock(spec=HybridRetrievalService)
    provider = make_provider(citation_ids=["S1"])

    result = await AnswerService(retriever).answer(
        AsyncMock(spec=AsyncSession),
        FakeEmbeddingProvider(),
        provider,
        knowledge_base_id=KB_ID,
        question="上海住宿费是多少？",
        expense_date=None,
        allowed_scopes=frozenset({"all_employees"}),
    )

    assert result.status == "needs_clarification"
    assert result.missing_information == ("expense_date",)
    retriever.search.assert_not_awaited()
    assert provider.prompts == []


@pytest.mark.asyncio
async def test_no_retrieved_evidence_refuses_without_model_call() -> None:
    retriever = AsyncMock(spec=HybridRetrievalService)
    retriever.search.return_value = HybridSearchResult(hits=(), version_conflicts=())
    provider = make_provider(citation_ids=["S1"])

    result = await AnswerService(retriever).answer(
        AsyncMock(spec=AsyncSession),
        FakeEmbeddingProvider(),
        provider,
        knowledge_base_id=KB_ID,
        question="法国住宿费是多少？",
        expense_date=date(2026, 5, 1),
        allowed_scopes=frozenset({"all_employees"}),
    )

    assert result.status == "refused"
    assert result.citations == ()
    assert provider.prompts == []


@pytest.mark.asyncio
async def test_answer_maps_known_citation_and_exposes_partial_extraction_warning() -> None:
    retriever = AsyncMock(spec=HybridRetrievalService)
    retriever.search.return_value = HybridSearchResult(
        hits=(make_hit(partial=True),),
        version_conflicts=(),
    )
    provider = make_provider(citation_ids=["S1"])

    result = await AnswerService(retriever).answer(
        AsyncMock(spec=AsyncSession),
        FakeEmbeddingProvider(),
        provider,
        knowledge_base_id=KB_ID,
        question="上海住宿费是多少？",
        expense_date=date(2026, 5, 1),
        allowed_scopes=frozenset({"all_employees"}),
    )

    assert result.status == "answered"
    assert result.citations[0].chunk_id == CHUNK_ID
    assert result.citations[0].source_id == "S1"
    assert result.evidence[0].hit.chunk_id == CHUNK_ID
    assert result.prompt_revision == "v1-structured-r2"
    assert result.warnings[0].code == "partial_text_extraction"
    assert result.usage.input_tokens == 100


@pytest.mark.asyncio
async def test_answer_rejects_citation_not_present_in_retrieved_context() -> None:
    retriever = AsyncMock(spec=HybridRetrievalService)
    retriever.search.return_value = HybridSearchResult(
        hits=(make_hit(),),
        version_conflicts=(),
    )
    provider = make_provider(citation_ids=["S2"])

    with pytest.raises(AnswerOutputError, match="S2"):
        await AnswerService(retriever).answer(
            AsyncMock(spec=AsyncSession),
            FakeEmbeddingProvider(),
            provider,
            knowledge_base_id=KB_ID,
            question="上海住宿费是多少？",
            expense_date=date(2026, 5, 1),
            allowed_scopes=frozenset({"all_employees"}),
        )


@pytest.mark.asyncio
async def test_bailian_provider_parses_json_and_records_usage_without_network() -> None:
    response = SimpleNamespace(
        content=json.dumps(
            {
                "status": "answered",
                "answer": "每人每晚最高六百元。",
                "citation_ids": ["S1"],
                "missing_information": [],
            },
            ensure_ascii=False,
        ),
        usage_metadata={"input_tokens": 88, "output_tokens": 16},
        response_metadata={},
    )
    model = AsyncMock()
    model.ainvoke.return_value = response
    provider = BailianAnswerProvider(
        model_name="qwen-plus",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("not-used-by-fake"),
        temperature=0,
        enable_thinking=False,
        timeout_seconds=10,
        max_retries=0,
        model=model,
    )
    prompt = build_answer_prompt(
        version="v1",
        question="上海住宿费是多少？",
        expense_date=date(2026, 5, 1),
        evidence=build_answer_evidence((make_hit(),)),
        version_conflicts=(),
    )

    generated = await provider.generate_answer(prompt)

    assert generated.draft.citation_ids == ["S1"]
    assert generated.usage == GenerationUsage(input_tokens=88, output_tokens=16)
    messages = model.ainvoke.await_args.args[0]
    assert messages[0].content == prompt.system
    assert messages[1].content == prompt.user


@pytest.mark.asyncio
async def test_bailian_provider_without_key_fails_safely_before_network() -> None:
    provider = BailianAnswerProvider(
        model_name="qwen-plus",
        base_url="https://example.invalid/v1",
        api_key=None,
        temperature=0,
        enable_thinking=False,
        timeout_seconds=10,
        max_retries=0,
    )
    prompt = build_answer_prompt(
        version="v0",
        question="上海住宿费是多少？",
        expense_date=date(2026, 5, 1),
        evidence=build_answer_evidence((make_hit(),)),
        version_conflicts=(),
    )

    with pytest.raises(AnswerProviderError, match="not configured"):
        await provider.generate_answer(prompt)


@pytest.mark.asyncio
async def test_bailian_provider_classifies_invalid_json_and_keeps_usage() -> None:
    response = SimpleNamespace(
        content="not-json",
        usage_metadata={"input_tokens": 42, "output_tokens": 3},
        response_metadata={},
    )
    model = AsyncMock()
    model.ainvoke.return_value = response
    provider = BailianAnswerProvider(
        model_name="qwen-plus",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("not-used-by-fake"),
        temperature=0,
        enable_thinking=False,
        timeout_seconds=10,
        max_retries=0,
        model=model,
    )
    prompt = build_answer_prompt(
        version="v1",
        question="上海住宿费是多少？",
        expense_date=date(2026, 5, 1),
        evidence=build_answer_evidence((make_hit(),)),
        version_conflicts=(),
    )

    with pytest.raises(AnswerProviderError) as captured:
        await provider.generate_answer(prompt)

    assert captured.value.code == "invalid_json"
    assert captured.value.usage == GenerationUsage(input_tokens=42, output_tokens=3)


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def test_public_answer_api_forces_employee_scope() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = MagicMock()
    service = AsyncMock(spec=AnswerService)
    service.answer.return_value = AnswerResult(
        status="answered",
        answer="普通情况下每人每晚最高六百元。",
        citations=(
            AnswerCitation(
                source_id="S1",
                chunk_id=CHUNK_ID,
                document_id=DOCUMENT_ID,
                original_filename="travel-policy.md",
                version_label="TRAVEL-2026.1",
                content="上海住宿费普通标准为每人每晚六百元。",
                page_start=None,
                page_end=None,
                section_path=("第四章 住宿费",),
            ),
        ),
        missing_information=(),
        warnings=(),
        version_conflicts=(),
        prompt_version="v1",
        provider_name="fixture",
        model_name="fixture-answer",
        usage=GenerationUsage(input_tokens=100, output_tokens=20),
    )
    provider = make_provider(citation_ids=["S1"])
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_answer_provider] = lambda: provider
    app.dependency_overrides[get_answer_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KB_ID}/answer",
                json={
                    "question": "上海住宿费是多少？",
                    "expense_date": "2026-05-01",
                    "top_k": 5,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["citations"][0]["source_id"] == "S1"
    assert service.answer.await_args.kwargs["allowed_scopes"] == PUBLIC_DOCUMENT_SCOPES


def test_public_answer_api_rejects_untrusted_model_citation() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = MagicMock()
    service = AsyncMock(spec=AnswerService)
    service.answer.side_effect = AnswerOutputError("Model cited sources that were not provided: S9")
    provider = make_provider(citation_ids=["S9"])
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_answer_provider] = lambda: provider
    app.dependency_overrides[get_answer_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KB_ID}/answer",
                json={
                    "question": "上海住宿费是多少？",
                    "expense_date": "2026-05-01",
                    "top_k": 5,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {"detail": "Answer model returned an untrusted citation"}
