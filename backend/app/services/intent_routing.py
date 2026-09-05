"""制度问答、报销状态与合规预检查之间的能力路由。

规则分类器和百炼 Function Calling 共用身份校验、工具契约、资源归属与审计边界。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.observability import trace_stage
from app.security.identity import AuthenticatedIdentity
from app.services.answer_prompts import PromptVersion
from app.services.answering import AnswerProvider, AnswerResult, AnswerService
from app.services.bailian_tool_provider import BailianIntentClassifier
from app.services.compliance_graph import ComplianceCheckResult, ComplianceCheckService
from app.services.embeddings import EmbeddingProvider
from app.services.reimbursement_status import (
    ReimbursementStatusResult,
    ReimbursementStatusTool,
)
from app.services.tool_contracts import (
    ReimbursementDetailToolArguments,
    ReimbursementStatusToolArguments,
    ToolCallDecision,
    extract_reimbursement_number,
)

Capability = Literal["policy_rag", "reimbursement_status", "compliance_precheck"]
class IntentClassifier(Protocol):
    def classify(self, question: str) -> ToolCallDecision | Awaitable[ToolCallDecision]: ...


class RuleBasedIntentClassifier:
    """无需外部模型且结果可复现的能力分类器。"""

    _status_terms = (
        "报销单",
        "报销状态",
        "审批状态",
        "审批到哪",
        "审核进度",
        "报销到哪",
        "我的报销",
    )
    _compliance_terms = (
        "合规",
        "符合",
        "符合规定",
        "能不能报销",
        "是否可以报销",
        "能否报销",
        "报销标准",
    )

    def classify(self, question: str) -> ToolCallDecision:
        reimbursement_number = extract_reimbursement_number(question)
        if any(term in question for term in self._compliance_terms):
            arguments = (
                ReimbursementDetailToolArguments(reimbursement_number=reimbursement_number)
                if reimbursement_number
                else None
            )
            return ToolCallDecision(capability="compliance_precheck", arguments=arguments)
        if not any(term in question for term in self._status_terms):
            return ToolCallDecision(capability="policy_rag")
        arguments = (
            ReimbursementStatusToolArguments(reimbursement_number=reimbursement_number)
            if reimbursement_number
            else None
        )
        return ToolCallDecision(capability="reimbursement_status", arguments=arguments)


@dataclass(frozen=True)
class RoutingResult:
    route: Capability
    status: Literal["answered", "refused", "needs_clarification", "failed"]
    answer: str
    missing_information: tuple[str, ...] = ()
    answer_result: AnswerResult | None = None
    reimbursement_result: ReimbursementStatusResult | None = None
    compliance_result: ComplianceCheckResult | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, str] | None = None
    tool_outcome: Literal["success", "not_found", "needs_clarification", "failed"] | None = None


class IntentRoutingService:
    """保持能力选择与业务执行分离，便于替换分类器而不放宽工具权限。"""

    def __init__(
        self,
        answer_service: AnswerService | None = None,
        reimbursement_tool: ReimbursementStatusTool | None = None,
        compliance_service: ComplianceCheckService | None = None,
        classifier: IntentClassifier | None = None,
    ) -> None:
        self.answer_service = answer_service or AnswerService()
        self.reimbursement_tool = reimbursement_tool or ReimbursementStatusTool()
        self.compliance_service = compliance_service or ComplianceCheckService(
            answer_service=self.answer_service
        )
        self.classifier = classifier or RuleBasedIntentClassifier()

    async def route(
        self,
        session: AsyncSession,
        identity: AuthenticatedIdentity,
        embedding_provider: EmbeddingProvider,
        answer_provider: AnswerProvider,
        *,
        knowledge_base_id: UUID,
        question: str,
        expense_date: date | None,
        top_k: int,
        prompt_version: PromptVersion = "v1",
        request_id: UUID | None = None,
    ) -> RoutingResult:
        request_id = request_id or uuid4()
        with trace_stage("intent", classifier=type(self.classifier).__name__) as intent_trace:
            decision = self.classifier.classify(question)
            if inspect.isawaitable(decision):
                decision = await decision
            intent_trace["capability"] = decision.capability
        if decision.capability == "compliance_precheck":
            return await self._route_compliance(
                session,
                identity,
                embedding_provider,
                answer_provider,
                knowledge_base_id=knowledge_base_id,
                question=question,
                decision=decision,
                top_k=top_k,
                prompt_version=prompt_version,
                request_id=request_id,
            )
        if decision.capability == "reimbursement_status":
            return await self._route_status(session, identity, decision, request_id)

        # 制度问题才把日期交给 AnswerService；状态问题不会被日期门槛拦截。
        result = await self.answer_service.answer(
            session,
            embedding_provider,
            answer_provider,
            knowledge_base_id=knowledge_base_id,
            question=question,
            expense_date=expense_date,
            allowed_scopes=identity.scopes,
            top_k=top_k,
            prompt_version=prompt_version,
        )
        return RoutingResult(
            route="policy_rag",
            status=result.status,
            answer=result.answer,
            missing_information=result.missing_information,
            answer_result=result,
        )

    async def _route_compliance(
        self,
        session: AsyncSession,
        identity: AuthenticatedIdentity,
        embedding_provider: EmbeddingProvider,
        answer_provider: AnswerProvider,
        *,
        knowledge_base_id: UUID,
        question: str,
        decision: ToolCallDecision,
        top_k: int,
        prompt_version: PromptVersion,
        request_id: UUID,
    ) -> RoutingResult:
        if decision.arguments is None:
            return RoutingResult(
                route="compliance_precheck",
                status="needs_clarification",
                answer="请提供报销单号，我只能读取当前登录用户本人的报销事实。",
                missing_information=("reimbursement_number",),
                tool_name="get_my_reimbursement_detail",
                tool_outcome="needs_clarification",
            )
        result = await self.compliance_service.check(
            session,
            identity,
            embedding_provider,
            answer_provider,
            knowledge_base_id=knowledge_base_id,
            question=question,
            reimbursement_number=decision.arguments.reimbursement_number,
            top_k=top_k,
            prompt_version=prompt_version,
            request_id=request_id,
        )
        tool_outcome = {
            "normal": "success",
            "not_found": "not_found",
            "tool_failed": "failed",
            "evidence_insufficient": "success",
        }[result.branch]
        return RoutingResult(
            route="compliance_precheck",
            status=result.status,
            answer=result.answer,
            missing_information=result.missing_information,
            answer_result=result.policy_result,
            compliance_result=result,
            tool_name="get_my_reimbursement_detail",
            tool_arguments={"reimbursement_number": decision.arguments.reimbursement_number},
            tool_outcome=tool_outcome,
        )

    async def _route_status(
        self,
        session: AsyncSession,
        identity: AuthenticatedIdentity,
        decision: ToolCallDecision,
        request_id: UUID,
    ) -> RoutingResult:
        if decision.arguments is None:
            return RoutingResult(
                route="reimbursement_status",
                status="needs_clarification",
                answer="请提供报销单号，我只能查询当前登录用户本人所属的报销单。",
                missing_information=("reimbursement_number",),
                tool_name="get_my_reimbursement_status",
                tool_outcome="needs_clarification",
            )
        arguments = decision.arguments.model_dump()
        result = await self.reimbursement_tool.execute(
            session,
            identity,
            reimbursement_number=decision.arguments.reimbursement_number,
            request_id=request_id,
        )
        return RoutingResult(
            route="reimbursement_status",
            status="answered" if result.status != "not_found" else "refused",
            answer=result.message,
            reimbursement_result=result,
            tool_name=self.reimbursement_tool.name,
            tool_arguments={key: str(value) for key, value in arguments.items()},
            tool_outcome=result.status if result.status == "not_found" else "success",
        )


def get_intent_routing_service() -> IntentRoutingService:
    settings = get_settings()
    classifier = None
    if settings.tool_routing_provider == "bailian":
        classifier = BailianIntentClassifier(
            model_name=settings.tool_routing_model_name,
            base_url=settings.answer_base_url,
            api_key=settings.dashscope_api_key,
            temperature=settings.tool_routing_temperature,
            timeout_seconds=settings.tool_routing_timeout_seconds,
            max_retries=settings.tool_routing_max_retries,
        )
    return IntentRoutingService(classifier=classifier)
