from collections.abc import AsyncIterator, Callable
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.main import app
from app.models.document import Document
from app.services.embeddings import (
    DocumentEmbeddingResult,
    DocumentEmbeddingService,
    get_document_embedding_service,
    get_embedding_provider,
)
from app.services.retrieval import (
    PUBLIC_DOCUMENT_SCOPES,
    DenseRetrievalService,
    DenseSearchHit,
    get_dense_retrieval_service,
)

KB_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
CHUNK_ID = UUID("1056bc33-2194-4965-b148-9bde6180db44")


class FakeProvider:
    provider_name = "fixture"
    model_name = "fixture-v1"
    dimension = 512


def override_db_session(
    session: AsyncSession,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    return override


def make_document() -> Document:
    document = Document(
        id=DOCUMENT_ID,
        knowledge_base_id=KB_ID,
        original_filename="policy.md",
        content_type="text/markdown",
        file_size=10,
        storage_key="documents/policy.md",
        sha256="a" * 64,
    )
    document.status = "ready"
    return document


def scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def test_embed_document_endpoint_returns_idempotency_counts() -> None:
    document = make_document()
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = scalar_result(document)
    embedder = AsyncMock(spec=DocumentEmbeddingService)
    embedder.index_document.return_value = DocumentEmbeddingResult(
        document_id=DOCUMENT_ID,
        provider_name="fixture",
        model_name="fixture-v1",
        dimension=512,
        total_chunks=3,
        embedded_chunks=1,
        skipped_chunks=2,
    )
    provider = FakeProvider()
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_document_embedding_service] = lambda: embedder

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KB_ID}/documents/{DOCUMENT_ID}/embeddings"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["embedded_chunks"] == 1
    assert response.json()["skipped_chunks"] == 2
    embedder.index_document.assert_awaited_once_with(document, session, provider)


def test_public_dense_search_never_accepts_client_selected_role() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = MagicMock()
    retriever = AsyncMock(spec=DenseRetrievalService)
    provider = FakeProvider()
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_dense_retrieval_service] = lambda: retriever

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KB_ID}/search",
                json={
                    "query": "财务内部阈值",
                    "expense_date": "2026-05-01",
                    "top_k": 5,
                    "role": "finance_reviewer",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    retriever.search.assert_not_awaited()


def test_public_dense_search_uses_employee_visible_scope() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = MagicMock()
    retriever = AsyncMock(spec=DenseRetrievalService)
    retriever.search.return_value = (
        DenseSearchHit(
            chunk_id=CHUNK_ID,
            document_id=DOCUMENT_ID,
            original_filename="policy.md",
            version_label="TRAVEL-2026.1",
            content="上海住宿费上限为每晚 600 元",
            page_start=None,
            page_end=None,
            section_path=("第四章", "住宿费"),
            cosine_distance=0.05,
        ),
    )
    provider = FakeProvider()
    app.dependency_overrides[get_db_session] = override_db_session(session)
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_dense_retrieval_service] = lambda: retriever

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KB_ID}/search",
                json={"query": "上海住宿标准", "expense_date": "2026-05-01", "top_k": 5},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["hits"][0]["similarity"] == 0.95
    call = retriever.search.await_args
    assert call.kwargs["allowed_scopes"] == PUBLIC_DOCUMENT_SCOPES
    assert call.kwargs["expense_date"] == date(2026, 5, 1)
