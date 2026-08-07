import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.embeddings import (
    DocumentEmbeddingService,
    DocumentNotReadyForEmbeddingError,
    EmbeddingProviderError,
    validate_embedding_vectors,
)
from app.services.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider

DOCUMENT_ID = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")


def vector(position: int = 0) -> tuple[float, ...]:
    values = [0.0] * 512
    values[position] = 1.0
    return tuple(values)


class FakeProvider:
    provider_name = "fixture"
    model_name = "fixture-v1"
    dimension = 512

    def __init__(self) -> None:
        self.embed_documents = AsyncMock()
        self.embed_query = AsyncMock()


def make_document(status: str = "ready") -> Document:
    document = Document(
        id=DOCUMENT_ID,
        knowledge_base_id=UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8"),
        original_filename="policy.md",
        content_type="text/markdown",
        file_size=10,
        storage_key="documents/policy.md",
        sha256="a" * 64,
    )
    document.status = status
    return document


def make_chunk(ordinal: int) -> DocumentChunk:
    return DocumentChunk(
        document_id=DOCUMENT_ID,
        ordinal=ordinal,
        content=f"制度正文 {ordinal}",
        page_start=None,
        page_end=None,
        section_path=["住宿费"],
        char_count=6,
        token_estimate=6,
        content_hash=str(ordinal) * 64,
        extraction_method="native_text",
        chunking_strategy="heading_page_v1",
        chunk_size=800,
        chunk_overlap=100,
    )


def session_with_chunks(chunks: list[DocumentChunk]) -> AsyncMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = chunks
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result
    return session


def test_embedding_contract_rejects_wrong_count_dimension_and_non_finite_values() -> None:
    with pytest.raises(EmbeddingProviderError, match="count"):
        validate_embedding_vectors((), expected_count=1, expected_dimension=512)
    with pytest.raises(EmbeddingProviderError, match="dimension"):
        validate_embedding_vectors(((1.0,),), expected_count=1, expected_dimension=512)
    with pytest.raises(EmbeddingProviderError, match="non-finite"):
        validate_embedding_vectors(
            ((float("nan"), *([0.0] * 511)),), expected_count=1, expected_dimension=512
        )


def test_local_model_path_does_not_replace_traceable_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded_from: list[str] = []

    class FakeSentenceTransformer:
        def __init__(self, model_path: str, *, device: str) -> None:
            loaded_from.append(model_path)
            assert device == "cpu"

        def get_embedding_dimension(self) -> int:
            return 512

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    provider = SentenceTransformerEmbeddingProvider(
        model_name="BAAI/bge-small-zh-v1.5",
        model_path="D:/models/bge-small-zh-v1.5",
        dimension=512,
        batch_size=8,
        query_instruction="检索：",
    )

    provider._get_model()

    assert loaded_from == ["D:/models/bge-small-zh-v1.5"]
    assert provider.model_name == "BAAI/bge-small-zh-v1.5"


@pytest.mark.asyncio
async def test_embedding_service_indexes_stale_chunks_atomically() -> None:
    chunks = [make_chunk(0), make_chunk(1)]
    session = session_with_chunks(chunks)
    provider = FakeProvider()
    provider.embed_documents.return_value = (vector(0), vector(1))

    result = await DocumentEmbeddingService(batch_size=8).index_document(
        make_document(), session, provider
    )

    assert result.embedded_chunks == 2
    assert result.skipped_chunks == 0
    assert chunks[0].embedding_provider == "fixture"
    assert chunks[0].embedding_content_hash == chunks[0].content_hash
    assert len(chunks[0].embedding or []) == 512
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_embedding_service_skips_current_model_and_content_hash() -> None:
    chunk = make_chunk(0)
    chunk.embedding = list(vector())
    chunk.embedding_provider = "fixture"
    chunk.embedding_model = "fixture-v1"
    chunk.embedding_dimension = 512
    chunk.embedding_content_hash = chunk.content_hash
    session = session_with_chunks([chunk])
    provider = FakeProvider()

    result = await DocumentEmbeddingService().index_document(make_document(), session, provider)

    assert result.embedded_chunks == 0
    assert result.skipped_chunks == 1
    provider.embed_documents.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_service_does_not_persist_half_finished_batches() -> None:
    chunks = [make_chunk(0), make_chunk(1)]
    session = session_with_chunks(chunks)
    provider = FakeProvider()
    provider.embed_documents.side_effect = [(vector(0),), EmbeddingProviderError("offline")]

    with pytest.raises(EmbeddingProviderError, match="offline"):
        await DocumentEmbeddingService(batch_size=1).index_document(
            make_document(), session, provider
        )

    assert all(chunk.embedding is None for chunk in chunks)
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_service_rejects_unparsed_document() -> None:
    session = session_with_chunks([])
    with pytest.raises(DocumentNotReadyForEmbeddingError, match="parsed successfully"):
        await DocumentEmbeddingService().index_document(
            make_document("pending"), session, FakeProvider()
        )
    session.execute.assert_not_awaited()
