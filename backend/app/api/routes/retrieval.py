from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.schemas.retrieval import DenseSearchHitResponse, DenseSearchRequest, DenseSearchResponse
from app.services.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    get_embedding_provider,
)
from app.services.retrieval import (
    PUBLIC_DOCUMENT_SCOPES,
    DenseRetrievalService,
    get_dense_retrieval_service,
)

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/search")
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
EmbeddingProviderDependency = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
DenseRetriever = Annotated[DenseRetrievalService, Depends(get_dense_retrieval_service)]


@router.post("", response_model=DenseSearchResponse, summary="Search public policy chunks")
async def dense_search(
    knowledge_base_id: UUID,
    payload: DenseSearchRequest,
    session: DatabaseSession,
    provider: EmbeddingProviderDependency,
    retriever: DenseRetriever,
) -> DenseSearchResponse:
    if await session.get(KnowledgeBase, knowledge_base_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    try:
        hits = await retriever.search(
            session,
            provider,
            knowledge_base_id=knowledge_base_id,
            query=payload.query,
            expense_date=payload.expense_date,
            allowed_scopes=PUBLIC_DOCUMENT_SCOPES,
            top_k=payload.top_k,
        )
    except EmbeddingProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return DenseSearchResponse(
        query=payload.query,
        expense_date=payload.expense_date,
        provider_name=provider.provider_name,
        model_name=provider.model_name,
        top_k=payload.top_k,
        searched_at=datetime.now(UTC),
        hits=[
            DenseSearchHitResponse(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                original_filename=hit.original_filename,
                version_label=hit.version_label,
                content=hit.content,
                page_start=hit.page_start,
                page_end=hit.page_end,
                section_path=list(hit.section_path),
                similarity=hit.similarity,
            )
            for hit in hits
        ],
    )
