"""合规预检查的最小 LangGraph 编排。

图只负责把已经鉴权的领域服务按顺序串起来：读取本人报销事实、按费用日期检索制度、
检查证据是否足够、组装结构化结果。图节点不接收用户身份参数，也不直接拼接 SQL。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import trace_stage
from app.security.identity import AuthenticatedIdentity
from app.services.answer_prompts import PromptVersion
from app.services.answering import AnswerProvider, AnswerResult, AnswerService
from app.services.embeddings import EmbeddingProvider
from app.services.reimbursement_detail import (
    ReimbursementDetailResult,
    ReimbursementDetailTool,
    ReimbursementDetailToolError,
)

ComplianceBranch = Literal[
    "normal",
    "not_found",
    "tool_failed",
    "evidence_insufficient",
]


@dataclass(frozen=True)
class ComplianceCheckResult:
    """合规预检查对外结果，明确区分事实、制度回答和系统建议。"""

    branch: ComplianceBranch
    status: Literal["answered", "refused", "needs_clarification", "failed"]
    answer: str
    recommendation: str
    missing_information: tuple[str, ...] = ()
    detail: ReimbursementDetailResult | None = None
    policy_result: AnswerResult | None = None
    trace: tuple[str, ...] = ()


class _ComplianceState(TypedDict, total=False):
    session: AsyncSession
    identity: AuthenticatedIdentity
    embedding_provider: EmbeddingProvider
    answer_provider: AnswerProvider
    knowledge_base_id: UUID
    question: str
    reimbursement_number: str
    top_k: int
    prompt_version: PromptVersion
    request_id: UUID
    detail: ReimbursementDetailResult | None
    policy_result: AnswerResult | None
    branch: ComplianceBranch
    error: str | None
    trace: list[str]
    result: ComplianceCheckResult


class ComplianceCheckService:
    """用可解释节点编排合规预检查，不让框架接管领域权限。"""

    def __init__(
        self,
        *,
        answer_service: AnswerService | None = None,
        detail_tool: ReimbursementDetailTool | None = None,
    ) -> None:
        self.answer_service = answer_service or AnswerService()
        self.detail_tool = detail_tool or ReimbursementDetailTool()
        self._graph = self._build_graph()

    async def check(
        self,
        session: AsyncSession,
        identity: AuthenticatedIdentity,
        embedding_provider: EmbeddingProvider,
        answer_provider: AnswerProvider,
        *,
        knowledge_base_id: UUID,
        question: str,
        reimbursement_number: str,
        top_k: int = 5,
        prompt_version: PromptVersion = "v1",
        request_id: UUID | None = None,
    ) -> ComplianceCheckResult:
        """执行图并只返回最终结构化结果，隐藏 LangGraph 内部状态。"""

        with trace_stage("compliance", workflow="langgraph") as graph_trace:
            state = await self._graph.ainvoke(
                {
                    "session": session,
                    "identity": identity,
                    "embedding_provider": embedding_provider,
                    "answer_provider": answer_provider,
                    "knowledge_base_id": knowledge_base_id,
                    "question": question,
                    "expense_date": None,
                    "reimbursement_number": reimbursement_number,
                    "top_k": top_k,
                    "prompt_version": prompt_version,
                    "request_id": request_id or uuid4(),
                    "trace": ["graph_started"],
                }
            )
            graph_trace["branch"] = state["result"].branch
        return state["result"]

    def _build_graph(self):
        graph = StateGraph(_ComplianceState)
        graph.add_node("load_detail", self._load_detail)
        graph.add_node("retrieve_policy", self._retrieve_policy)
        graph.add_node("check_evidence", self._check_evidence)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "load_detail")
        graph.add_conditional_edges(
            "load_detail",
            self._after_detail,
            {"retrieve_policy": "retrieve_policy", "finalize": "finalize"},
        )
        graph.add_edge("retrieve_policy", "check_evidence")
        graph.add_edge("check_evidence", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    async def _load_detail(self, state: _ComplianceState) -> dict[str, object]:
        trace = [*state.get("trace", []), "detail_lookup_started"]
        try:
            detail = await self.detail_tool.execute(
                state["session"],
                state["identity"],
                reimbursement_number=state["reimbursement_number"],
                request_id=state["request_id"],
            )
        except ReimbursementDetailToolError:
            return {
                "branch": "tool_failed",
                "error": "detail_tool_failed",
                "trace": [*trace, "detail_lookup_failed"],
            }
        if detail.status == "not_found":
            return {
                "detail": detail,
                "branch": "not_found",
                "trace": [*trace, "detail_not_found"],
            }
        return {"detail": detail, "trace": [*trace, "detail_loaded"]}

    @staticmethod
    def _after_detail(state: _ComplianceState) -> str:
        return (
            "finalize"
            if state.get("branch") in {"not_found", "tool_failed"}
            else "retrieve_policy"
        )

    async def _retrieve_policy(self, state: _ComplianceState) -> dict[str, object]:
        detail = state["detail"]
        if detail is None or detail.expense_date is None:
            return {
                "branch": "evidence_insufficient",
                "trace": [*state.get("trace", []), "expense_date_missing"],
            }
        try:
            policy_result = await self.answer_service.answer(
                state["session"],
                state["embedding_provider"],
                state["answer_provider"],
                knowledge_base_id=state["knowledge_base_id"],
                question=state["question"],
                expense_date=detail.expense_date,
                allowed_scopes=state["identity"].scopes,
                top_k=state["top_k"],
                prompt_version=state["prompt_version"],
            )
        except Exception:
            # 不把数据库、模型或网络异常细节写入用户响应，保留稳定分支供监控和重试。
            return {
                "branch": "tool_failed",
                "error": "policy_retrieval_failed",
                "trace": [*state.get("trace", []), "policy_lookup_failed"],
            }
        return {
            "policy_result": policy_result,
            "trace": [*state.get("trace", []), "policy_lookup_completed"],
        }

    @staticmethod
    def _check_evidence(state: _ComplianceState) -> dict[str, object]:
        policy_result = state.get("policy_result")
        if policy_result is None or not policy_result.citations:
            return {
                "branch": "evidence_insufficient",
                "trace": [*state.get("trace", []), "evidence_insufficient"],
            }
        return {
            "branch": "normal",
            "trace": [*state.get("trace", []), "evidence_verified"],
        }

    @staticmethod
    def _finalize(state: _ComplianceState) -> dict[str, object]:
        branch = state.get("branch", "tool_failed")
        detail = state.get("detail")
        policy_result = state.get("policy_result")
        trace = tuple([*state.get("trace", []), "result_finalized"])
        if branch == "normal" and policy_result is not None and detail is not None:
            return {
                "result": ComplianceCheckResult(
                    branch="normal",
                    status="answered" if policy_result.status == "answered" else "refused",
                    answer=policy_result.answer,
                    recommendation="制度证据已返回；最终报销是否通过仍以财务审核为准。",
                    missing_information=policy_result.missing_information,
                    detail=detail,
                    policy_result=policy_result,
                    trace=trace,
                )
            }
        if branch == "not_found":
            return {
                "result": ComplianceCheckResult(
                    branch="not_found",
                    status="refused",
                    answer="未找到属于当前登录用户的报销单，不能进行合规预检查。",
                    recommendation="请核对报销单号，不能查询其他用户的报销数据。",
                    detail=detail,
                    trace=trace,
                )
            }
        if branch == "evidence_insufficient":
            return {
                "result": ComplianceCheckResult(
                    branch="evidence_insufficient",
                    status="refused",
                    answer="已读取本人报销事实，但当前知识库没有足够的适用制度证据，不能给出合规结论。",
                    recommendation="请补充或维护对应生效日期的制度材料后再检查。",
                    detail=detail,
                    policy_result=policy_result,
                    trace=trace,
                )
            }
        return {
            "result": ComplianceCheckResult(
                branch="tool_failed",
                status="failed",
                answer="合规预检查暂时失败，系统没有生成合规结论。",
                recommendation="请稍后重试；失败期间不要根据本次结果提交报销。",
                detail=detail,
                policy_result=policy_result,
                trace=trace,
            )
        }


def get_compliance_check_service() -> ComplianceCheckService:
    return ComplianceCheckService()
