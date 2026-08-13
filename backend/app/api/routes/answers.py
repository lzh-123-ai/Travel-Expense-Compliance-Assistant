from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.schemas.answer import (
    AnswerCitationResponse,
    AnswerRequest,
    AnswerResponse,
    AnswerVersionConflictResponse,
    AnswerWarningResponse,
)
from app.services.answering import AnswerOutputError, AnswerProvider, AnswerService
from app.services.bailian_answer_provider import AnswerProviderError, get_answer_provider
from app.services.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    get_embedding_provider,
)
from app.services.retrieval import PUBLIC_DOCUMENT_SCOPES, get_hybrid_retrieval_service

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/answer")
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
EmbeddingProviderDependency = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
AnswerProviderDependency = Annotated[AnswerProvider, Depends(get_answer_provider)]


def get_answer_service() -> AnswerService:
    return AnswerService(get_hybrid_retrieval_service())


AnswerServiceDependency = Annotated[AnswerService, Depends(get_answer_service)]


@router.post("", response_model=AnswerResponse, summary="Answer from public policy evidence")
async def answer_question(
    knowledge_base_id: UUID,
    payload: AnswerRequest,
    session: DatabaseSession,
    embedding_provider: EmbeddingProviderDependency,
    answer_provider: AnswerProviderDependency,
    service: AnswerServiceDependency,
) -> AnswerResponse:
    if await session.get(KnowledgeBase, knowledge_base_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    settings = get_settings()
    try:
        result = await service.answer(
            session,
            embedding_provider,
            answer_provider,
            knowledge_base_id=knowledge_base_id,
            question=payload.question,
            expense_date=payload.expense_date,
            allowed_scopes=PUBLIC_DOCUMENT_SCOPES,
            top_k=payload.top_k,
            prompt_version=settings.answer_prompt_version,
        )
    except (EmbeddingProviderError, AnswerProviderError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AnswerOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer model returned an untrusted citation",
        ) from exc
    return AnswerResponse(
        status=result.status,
        answer=result.answer,
        citations=[
            AnswerCitationResponse(
                source_id=citation.source_id,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                original_filename=citation.original_filename,
                version_label=citation.version_label,
                content=citation.content,
                page_start=citation.page_start,
                page_end=citation.page_end,
                section_path=list(citation.section_path),
            )
            for citation in result.citations
        ],
        missing_information=list(result.missing_information),
        warnings=[
            AnswerWarningResponse(
                code=warning.code,
                message=warning.message,
                document_id=warning.document_id,
            )
            for warning in result.warnings
        ],
        version_conflicts=[
            AnswerVersionConflictResponse(
                policy_type=conflict.policy_type,
                version_labels=list(conflict.version_labels),
            )
            for conflict in result.version_conflicts
        ],
        prompt_version=result.prompt_version,
        prompt_revision=result.prompt_revision,
        provider_name=result.provider_name,
        model_name=result.model_name,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )
