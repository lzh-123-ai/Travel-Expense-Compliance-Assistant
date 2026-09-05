"""制度问答、报销状态和合规预检查的统一助手入口。"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.schemas.answer import (
    AnswerCitationResponse,
    AnswerVersionConflictResponse,
    AnswerWarningResponse,
)
from app.schemas.assistant import (
    AssistantRequest,
    AssistantResponse,
    ComplianceCheckResponse,
    ReimbursementStatusResponse,
    ToolCallResponse,
)
from app.security.identity import AuthenticatedIdentity, get_current_identity
from app.services.answering import AnswerOutputError, AnswerProvider
from app.services.bailian_answer_provider import AnswerProviderError, get_answer_provider
from app.services.bailian_tool_provider import ToolRoutingProviderError
from app.services.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    get_embedding_provider,
)
from app.services.intent_routing import (
    IntentRoutingService,
    RoutingResult,
    get_intent_routing_service,
)

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/assistant")
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
IdentityDependency = Annotated[AuthenticatedIdentity, Depends(get_current_identity)]
EmbeddingProviderDependency = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
AnswerProviderDependency = Annotated[AnswerProvider, Depends(get_answer_provider)]
IntentRouterDependency = Annotated[IntentRoutingService, Depends(get_intent_routing_service)]


def _response_from_result(result: RoutingResult, request_id: UUID) -> AssistantResponse:
    answer_result = result.answer_result
    reimbursement_result = result.reimbursement_result
    if reimbursement_result is None and result.compliance_result is not None:
        reimbursement_result = result.compliance_result.detail
    return AssistantResponse(
        route=result.route,
        status=result.status,
        answer=result.answer,
        missing_information=list(result.missing_information),
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
            for citation in (answer_result.citations if answer_result else ())
        ],
        warnings=[
            AnswerWarningResponse(
                code=warning.code,
                message=warning.message,
                document_id=warning.document_id,
            )
            for warning in (answer_result.warnings if answer_result else ())
        ],
        version_conflicts=[
            AnswerVersionConflictResponse(
                policy_type=conflict.policy_type,
                version_labels=list(conflict.version_labels),
            )
            for conflict in (answer_result.version_conflicts if answer_result else ())
        ],
        reimbursement=(
            ReimbursementStatusResponse(
                claim_number=reimbursement_result.claim_number,
                expense_date=reimbursement_result.expense_date,
                amount=reimbursement_result.amount,
                currency=reimbursement_result.currency,
                status=reimbursement_result.status,
                current_step=reimbursement_result.current_step,
                return_reason=reimbursement_result.return_reason,
            )
            if reimbursement_result
            else None
        ),
        compliance=(
            ComplianceCheckResponse(
                branch=result.compliance_result.branch,
                recommendation=result.compliance_result.recommendation,
                trace=list(result.compliance_result.trace),
            )
            if result.compliance_result
            else None
        ),
        tool_call=(
            ToolCallResponse(
                tool_name=result.tool_name,
                arguments=result.tool_arguments or {},
                outcome=result.tool_outcome or "failed",
            )
            if result.tool_name
            else None
        ),
        request_id=request_id,
    )


@router.post(
    "", response_model=AssistantResponse, summary="Route policy questions and status tools"
)
async def assistant_question(
    knowledge_base_id: UUID,
    payload: AssistantRequest,
    request: Request,
    session: DatabaseSession,
    identity: IdentityDependency,
    embedding_provider: EmbeddingProviderDependency,
    answer_provider: AnswerProviderDependency,
    service: IntentRouterDependency,
) -> AssistantResponse:
    """把能力选择交给服务，把用户身份保留在服务端依赖中。"""
    if await session.get(KnowledgeBase, knowledge_base_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    request_id = getattr(request.state, "request_id", uuid4())
    try:
        result = await service.route(
            session,
            identity,
            embedding_provider,
            answer_provider,
            knowledge_base_id=knowledge_base_id,
            question=payload.question,
            expense_date=payload.expense_date,
            top_k=payload.top_k,
            prompt_version=get_settings().answer_prompt_version,
            request_id=request_id,
        )
    except (EmbeddingProviderError, AnswerProviderError, ToolRoutingProviderError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except AnswerOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer model returned an untrusted citation",
        ) from exc
    return _response_from_result(result, request_id)
