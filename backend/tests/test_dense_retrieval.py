from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.retrieval import DenseRetrievalService

KB_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
CHUNK_ID = UUID("1056bc33-2194-4965-b148-9bde6180db44")


class FakeProvider:
    provider_name = "fixture"
    model_name = "fixture-v1"
    dimension = 512

    def __init__(self) -> None:
        values = [0.0] * 512
        values[0] = 1.0
        self.embed_query = AsyncMock(return_value=tuple(values))


def make_row() -> tuple[DocumentChunk, Document, float]:
    document = Document(
        id=DOCUMENT_ID,
        knowledge_base_id=KB_ID,
        original_filename="policy.md",
        content_type="text/markdown",
        file_size=10,
        storage_key="documents/policy.md",
        sha256="a" * 64,
        version_label="TRAVEL-2026.1",
    )
    chunk = DocumentChunk(
        id=CHUNK_ID,
        document_id=DOCUMENT_ID,
        ordinal=0,
        content="上海住宿费每晚 600 元",
        page_start=None,
        page_end=None,
        section_path=["第四章", "住宿费"],
        char_count=14,
        token_estimate=14,
        content_hash="b" * 64,
        extraction_method="native_text",
        chunking_strategy="heading_page_v1",
        chunk_size=800,
        chunk_overlap=100,
    )
    return chunk, document, 0.08


@pytest.mark.asyncio
async def test_dense_retrieval_applies_tenant_date_scope_and_model_filters() -> None:
    result = MagicMock()
    result.all.return_value = [make_row()]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result
    provider = FakeProvider()

    hits = await DenseRetrievalService().search(
        session,
        provider,
        knowledge_base_id=KB_ID,
        query="上海住宿标准",
        expense_date=date(2026, 5, 1),
        allowed_scopes=frozenset({"all_employees"}),
        top_k=5,
    )

    assert hits[0].chunk_id == CHUNK_ID
    assert hits[0].similarity == pytest.approx(0.92)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    for required in (
        "documents.knowledge_base_id",
        "documents.effective_from",
        "documents.effective_to",
        "documents.access_scope",
        "document_chunks.embedding_provider",
        "document_chunks.embedding_model",
        "document_chunks.embedding_content_hash = document_chunks.content_hash",
    ):
        assert required in sql


@pytest.mark.asyncio
async def test_dense_retrieval_with_no_allowed_scope_returns_without_embedding() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = FakeProvider()

    hits = await DenseRetrievalService().search(
        session,
        provider,
        knowledge_base_id=KB_ID,
        query="内部阈值",
        expense_date=date(2026, 5, 1),
        allowed_scopes=frozenset(),
        top_k=5,
    )

    assert hits == ()
    provider.embed_query.assert_not_awaited()
    session.execute.assert_not_awaited()
