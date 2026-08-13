"""dense、关键词和混合检索的 HTTP 诊断入口。

所有 Route 都将权限范围和日期传给 Service。Stage 12 必须用服务端身份替换当前
临时公共权限，绝不能相信客户端传来的角色。
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.schemas.retrieval import (
    DenseSearchHitResponse,
    DenseSearchRequest,
    DenseSearchResponse,
    HybridSearchHitResponse,
    HybridSearchResponse,
    KeywordSearchHitResponse,
    KeywordSearchResponse,
    VersionConflictResponse,
)
from app.services.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    get_embedding_provider,
)
from app.services.retrieval import (
    PUBLIC_DOCUMENT_SCOPES,
    DenseRetrievalService,
    HybridRetrievalService,
    KeywordRetrievalService,
    get_dense_retrieval_service,
    get_hybrid_retrieval_service,
    get_keyword_retrieval_service,
)

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/search")
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
EmbeddingProviderDependency = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
DenseRetriever = Annotated[DenseRetrievalService, Depends(get_dense_retrieval_service)]
KeywordRetriever = Annotated[KeywordRetrievalService, Depends(get_keyword_retrieval_service)]
HybridRetriever = Annotated[HybridRetrievalService, Depends(get_hybrid_retrieval_service)]


@router.post("", response_model=DenseSearchResponse, summary="Search public policy chunks")
async def dense_search(
    knowledge_base_id: UUID,
    payload: DenseSearchRequest,
    session: DatabaseSession,
    provider: EmbeddingProviderDependency,
    retriever: DenseRetriever,
) -> DenseSearchResponse:
    """运行 dense 诊断；向量排序前已执行权限范围过滤。"""
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
                policy_type=hit.policy_type,
                effective_from=hit.effective_from,
                effective_to=hit.effective_to,
            )
            for hit in hits
        ],
    )


@router.post(
    "/keyword", response_model=KeywordSearchResponse, summary="Search policy chunks by keywords"
)
async def keyword_search(
    knowledge_base_id: UUID,
    payload: DenseSearchRequest,
    session: DatabaseSession,
    retriever: KeywordRetriever,
) -> KeywordSearchResponse:
    """运行关键词诊断，使用相同的制度权限范围和日期要求。"""
    if await session.get(KnowledgeBase, knowledge_base_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    hits = await retriever.search(
        session,
        knowledge_base_id=knowledge_base_id,
        query=payload.query,
        expense_date=payload.expense_date,
        allowed_scopes=PUBLIC_DOCUMENT_SCOPES,
        top_k=payload.top_k,
    )
    return KeywordSearchResponse(
        query=payload.query,
        expense_date=payload.expense_date,
        tokenizer=retriever.tokenizer,
        top_k=payload.top_k,
        searched_at=datetime.now(UTC),
        hits=[
            KeywordSearchHitResponse(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                original_filename=hit.original_filename,
                version_label=hit.version_label,
                content=hit.content,
                page_start=hit.page_start,
                page_end=hit.page_end,
                section_path=list(hit.section_path),
                keyword_score=hit.keyword_score,
                policy_type=hit.policy_type,
                effective_from=hit.effective_from,
                effective_to=hit.effective_to,
            )
            for hit in hits
        ],
    )


@router.post(
    "/hybrid",
    response_model=HybridSearchResponse,
    summary="Search policy chunks with dense and keyword fusion",
)
async def hybrid_search(
    knowledge_base_id: UUID,
    payload: DenseSearchRequest,
    session: DatabaseSession,
    provider: EmbeddingProviderDependency,
    retriever: HybridRetriever,
) -> HybridSearchResponse:
    """运行融合诊断，并暴露排序信息供评测和排错。"""
    if await session.get(KnowledgeBase, knowledge_base_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    try:
        result = await retriever.search(
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
    return HybridSearchResponse(
        query=payload.query,
        expense_date=payload.expense_date,
        provider_name=provider.provider_name,
        model_name=provider.model_name,
        tokenizer=retriever.keyword.tokenizer,
        top_k=payload.top_k,
        searched_at=datetime.now(UTC),
        version_conflicts=[
            VersionConflictResponse(
                policy_type=conflict.policy_type,
                version_labels=list(conflict.version_labels),
            )
            for conflict in result.version_conflicts
        ],
        hits=[
            HybridSearchHitResponse(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                original_filename=hit.original_filename,
                version_label=hit.version_label,
                content=hit.content,
                page_start=hit.page_start,
                page_end=hit.page_end,
                section_path=list(hit.section_path),
                dense_similarity=hit.dense_similarity,
                keyword_score=hit.keyword_score,
                dense_rank=hit.dense_rank,
                keyword_rank=hit.keyword_rank,
                rrf_score=hit.rrf_score,
                document_rrf_score=hit.document_rrf_score,
                policy_type=hit.policy_type,
                effective_from=hit.effective_from,
                effective_to=hit.effective_to,
            )
            for hit in result.hits
        ],
    )
