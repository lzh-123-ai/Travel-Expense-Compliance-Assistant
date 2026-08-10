import json
from collections.abc import AsyncIterator, Callable
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.evaluation.loader import load_retrieval_eval_dataset
from app.main import app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.embeddings import get_embedding_provider
from app.services.keyword_indexing import DocumentKeywordIndexingService
from app.services.keywording import KEYWORD_TOKENIZER_VERSION, searchable_text, tokenize
from app.services.retrieval import (
    PUBLIC_DOCUMENT_SCOPES,
    DenseRetrievalService,
    DenseSearchHit,
    HybridRetrievalService,
    HybridSearchHit,
    HybridSearchResult,
    KeywordRetrievalService,
    KeywordSearchHit,
    VersionConflict,
    get_hybrid_retrieval_service,
)

KB_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
DOCUMENT_V1_ID = UUID("5b51df9d-1e70-4a8e-a016-2d4dabd2f9b4")
CHUNK_ID = UUID("1056bc33-2194-4965-b148-9bde6180db44")
CHUNK_V1_ID = UUID("2167cd44-32a5-4f24-8a2f-b036436c3710")
CHUNK_NOTICE_ID = UUID("3278de55-43b6-4035-9b30-c147547d4821")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE9_DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage9_retrieval_v0.json"
STAGE10_DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage10_retrieval_v0.json"
MANIFEST_PATH = PROJECT_ROOT / "data" / "policies" / "manifest.json"
STAGE10_REPORT_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "results" / "stage10_retrieval_comparison.json"
)


class FakeProvider:
    provider_name = "fixture"
    model_name = "fixture-v1"
    dimension = 512


def make_document(
    *,
    document_id: UUID = DOCUMENT_ID,
    version_label: str = "TRAVEL-2026.1",
    policy_type: str = "travel_policy",
) -> Document:
    document = Document(
        id=document_id,
        knowledge_base_id=KB_ID,
        original_filename=f"{version_label}.md",
        content_type="text/markdown",
        file_size=20,
        storage_key=f"documents/{document_id}.md",
        sha256="a" * 64,
        policy_type=policy_type,
        version_label=version_label,
        effective_from=date(2026, 1, 1),
        access_scope="all_employees",
    )
    document.status = "ready"
    return document


def make_chunk(
    *,
    chunk_id: UUID = CHUNK_ID,
    document_id: UUID = DOCUMENT_ID,
    content: str = "上海住宿费上限为每晚六百元",
) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        document_id=document_id,
        ordinal=0,
        content=content,
        page_start=None,
        page_end=None,
        section_path=["住宿费"],
        char_count=len(content),
        token_estimate=len(content),
        content_hash="b" * 64,
        extraction_method="native_text",
        chunking_strategy="heading_page_v1",
        chunk_size=800,
        chunk_overlap=100,
    )


def test_domain_tokenizer_is_deterministic_and_keeps_numbers() -> None:
    text = "上海住宿费 760 元，电子发票需要附件"

    assert tokenize(text) == tokenize(text)
    assert "住宿费" in tokenize(text)
    assert "760" in tokenize(text)
    assert searchable_text(text) == " ".join(tokenize(text))


def test_stage10_dataset_contains_forty_reviewed_retrieval_cases() -> None:
    stage9 = load_retrieval_eval_dataset(STAGE9_DATASET_PATH)
    stage10 = load_retrieval_eval_dataset(STAGE10_DATASET_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    known_labels = {document["version_label"] for document in manifest["documents"]}

    assert stage10.dataset_version == "stage10-retrieval-v0"
    assert stage10.annotation_status == "reviewed"
    assert len(stage10.cases) == 40
    assert [case.model_dump() for case in stage10.cases[:30]] == [
        case.model_dump() for case in stage9.cases
    ]
    assert {case.id for case in stage10.cases[30:]} == {
        f"HYBRID-EVAL-{number:03d}" for number in range(31, 41)
    }
    for case in stage10.cases:
        assert set(case.expected_version_labels) <= known_labels
        assert set(case.forbidden_version_labels) <= known_labels

    cases = {case.id: case for case in stage10.cases}
    assert cases["HYBRID-EVAL-037"].allowed_scopes == [
        "all_employees",
        "finance_only",
    ]
    assert cases["HYBRID-EVAL-038"].forbidden_version_labels == ["AUDIT-2026.1"]


def test_stage10_formal_report_compares_all_strategies_without_hiding_failures() -> None:
    dataset = load_retrieval_eval_dataset(STAGE10_DATASET_PATH)
    report = json.loads(STAGE10_REPORT_PATH.read_text(encoding="utf-8"))

    assert report["dataset_version"] == dataset.dataset_version
    assert report["dataset_annotation_status"] == "reviewed"
    assert report["formal_baseline"] is True
    assert set(report["strategies"]) == {"dense", "keyword", "hybrid"}
    expected_ids = {case.id for case in dataset.cases}
    for strategy in report["strategies"].values():
        assert {case["case_id"] for case in strategy["cases"]} == expected_ids
        assert strategy["aggregate"]["forbidden_filter_accuracy"] == 1.0

    assert report["strategies"]["dense"]["aggregate"]["case_pass_rate"] == 1.0
    assert report["strategies"]["hybrid"]["aggregate"]["case_pass_rate"] == 1.0
    assert report["strategies"]["keyword"]["aggregate"]["case_pass_rate"] < 1.0


@pytest.mark.asyncio
async def test_keyword_indexing_is_content_hash_idempotent() -> None:
    document = make_document()
    chunk = make_chunk()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [chunk]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result
    service = DocumentKeywordIndexingService()

    first = await service.index_document(document, session)
    second = await service.index_document(document, session)

    assert first.indexed_chunks == 1
    assert second.indexed_chunks == 0
    assert second.skipped_chunks == 1
    assert chunk.keyword_content_hash == chunk.content_hash
    assert chunk.keyword_tokenizer == KEYWORD_TOKENIZER_VERSION
    assert "住宿费" in chunk.keyword_search_text
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_keyword_retrieval_applies_business_and_index_filters() -> None:
    document = make_document()
    chunk = make_chunk()
    result = MagicMock()
    result.all.return_value = [(chunk, document, 0.42)]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    hits = await KeywordRetrievalService().search(
        session,
        knowledge_base_id=KB_ID,
        query="上海住宿费上限",
        expense_date=date(2026, 5, 1),
        allowed_scopes=PUBLIC_DOCUMENT_SCOPES,
        top_k=5,
    )

    assert hits[0].keyword_score == pytest.approx(0.42)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    for required in (
        "documents.knowledge_base_id",
        "documents.effective_from",
        "documents.effective_to",
        "documents.access_scope",
        "document_chunks.keyword_tokenizer",
        "document_chunks.keyword_content_hash = document_chunks.content_hash",
        "to_tsvector",
        "websearch_to_tsquery",
    ):
        assert required in sql


@pytest.mark.asyncio
async def test_hybrid_rrf_rewards_shared_hits_and_reports_version_conflicts() -> None:
    dense = AsyncMock(spec=DenseRetrievalService)
    keyword = AsyncMock(spec=KeywordRetrievalService)
    dense.search.return_value = (
        DenseSearchHit(
            chunk_id=CHUNK_V1_ID,
            document_id=DOCUMENT_V1_ID,
            original_filename="travel-v1.md",
            version_label="TRAVEL-2025.1",
            content="旧版住宿标准",
            page_start=None,
            page_end=None,
            section_path=("住宿费",),
            cosine_distance=0.05,
            policy_type="travel_policy",
        ),
        DenseSearchHit(
            chunk_id=CHUNK_ID,
            document_id=DOCUMENT_ID,
            original_filename="travel-v2.md",
            version_label="TRAVEL-2026.1",
            content="新版住宿标准",
            page_start=None,
            page_end=None,
            section_path=("住宿费",),
            cosine_distance=0.08,
            policy_type="travel_policy",
        ),
    )
    keyword.search.return_value = (
        KeywordSearchHit(
            chunk_id=CHUNK_ID,
            document_id=DOCUMENT_ID,
            original_filename="travel-v2.md",
            version_label="TRAVEL-2026.1",
            content="新版住宿标准",
            page_start=None,
            page_end=None,
            section_path=("住宿费",),
            keyword_score=0.9,
            policy_type="travel_policy",
        ),
        KeywordSearchHit(
            chunk_id=CHUNK_NOTICE_ID,
            document_id=UUID("61f290bc-5c81-4d9f-8192-67e0451a9a34"),
            original_filename="notice.md",
            version_label="HOTEL-SUP-2026.1",
            content="展会住宿临时标准",
            page_start=None,
            page_end=None,
            section_path=("临时标准",),
            keyword_score=0.7,
            policy_type="supplemental_notice",
        ),
    )
    service = HybridRetrievalService(dense=dense, keyword=keyword)

    result = await service.search(
        AsyncMock(spec=AsyncSession),
        FakeProvider(),
        knowledge_base_id=KB_ID,
        query="住宿标准",
        expense_date=date(2026, 5, 1),
        allowed_scopes=PUBLIC_DOCUMENT_SCOPES,
        top_k=3,
    )

    assert result.hits[0].chunk_id == CHUNK_ID
    assert result.hits[0].dense_rank == 2
    assert result.hits[0].keyword_rank == 1
    assert result.version_conflicts == (
        VersionConflict(
            policy_type="travel_policy",
            version_labels=("TRAVEL-2025.1", "TRAVEL-2026.1"),
        ),
    )
    assert dense.search.await_args.kwargs["top_k"] == 20
    assert keyword.search.await_args.kwargs["allowed_scopes"] == PUBLIC_DOCUMENT_SCOPES


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def test_public_hybrid_api_forces_employee_scope() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = MagicMock()
    retriever = AsyncMock(spec=HybridRetrievalService)
    retriever.keyword = MagicMock(tokenizer=KEYWORD_TOKENIZER_VERSION)
    retriever.search.return_value = HybridSearchResult(
        hits=(
            HybridSearchHit(
                chunk_id=CHUNK_ID,
                document_id=DOCUMENT_ID,
                original_filename="travel-v2.md",
                version_label="TRAVEL-2026.1",
                content="新版住宿标准",
                page_start=None,
                page_end=None,
                section_path=("住宿费",),
                dense_similarity=0.92,
                keyword_score=0.8,
                dense_rank=1,
                keyword_rank=1,
                rrf_score=2 / 61,
                policy_type="travel_policy",
            ),
        ),
        version_conflicts=(),
    )
    provider = FakeProvider()
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_hybrid_retrieval_service] = lambda: retriever

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KB_ID}/search/hybrid",
                json={"query": "住宿标准", "expense_date": "2026-05-01", "top_k": 5},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["hits"][0]["dense_rank"] == 1
    assert retriever.search.await_args.kwargs["allowed_scopes"] == PUBLIC_DOCUMENT_SCOPES
