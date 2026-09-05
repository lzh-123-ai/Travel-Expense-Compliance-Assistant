"""统一助手能力路由的 HTTP 输入输出契约。"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.answer import (
    AnswerCitationResponse,
    AnswerVersionConflictResponse,
    AnswerWarningResponse,
)


class AssistantRequest(BaseModel):
    """用户只提交问题和制度日期；身份从 Authorization 令牌派生。"""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(min_length=4, max_length=2000)]
    expense_date: date | None = None
    top_k: Annotated[int, Field(ge=1, le=10)] = 5


class ToolCallResponse(BaseModel):
    """向前端展示的工具轨迹；参数只包含工具允许的业务参数。"""

    tool_name: str
    arguments: dict[str, str]
    outcome: Literal["success", "not_found", "needs_clarification", "failed"]


class ReimbursementStatusResponse(BaseModel):
    claim_number: str
    expense_date: date | None = None
    amount: float | None = None
    currency: str | None = None
    status: str
    current_step: str | None = None
    return_reason: str | None = None


class ComplianceCheckResponse(BaseModel):
    """合规预检查的结构化边界：分支、建议和可解释节点轨迹。"""

    branch: Literal["normal", "not_found", "tool_failed", "evidence_insufficient"]
    recommendation: str
    trace: list[str]


class AssistantResponse(BaseModel):
    """各类助手能力共用的响应外壳。"""

    route: Literal["policy_rag", "reimbursement_status", "compliance_precheck"]
    status: Literal["answered", "refused", "needs_clarification", "failed"]
    answer: str
    missing_information: list[str]
    citations: list[AnswerCitationResponse]
    warnings: list[AnswerWarningResponse]
    version_conflicts: list[AnswerVersionConflictResponse]
    reimbursement: ReimbursementStatusResponse | None = None
    compliance: ComplianceCheckResponse | None = None
    tool_call: ToolCallResponse | None = None
    request_id: UUID
