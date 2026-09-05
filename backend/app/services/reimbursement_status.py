"""本人报销状态只读工具。

工具层负责参数校验、``employee_id`` 归属过滤和审计；它不接收模型传入的用户身份，
也不暴露通用 SQL 执行能力。查询不到记录时统一返回安全的 ``not_found``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import trace_stage
from app.models.reimbursement import Reimbursement
from app.models.tool_audit import ToolAudit
from app.security.identity import AuthenticatedIdentity
from app.services.tool_contracts import ReimbursementStatusToolArguments

TOOL_NAME = "get_my_reimbursement_status"


@dataclass(frozen=True)
class ReimbursementStatusResult:
    claim_number: str
    status: str
    message: str
    expense_date: date | None = None
    amount: float | None = None
    currency: str | None = None
    current_step: str | None = None
    return_reason: str | None = None


class ReimbursementStatusToolError(RuntimeError):
    """工具查询失败，路由层应将其转换为可重试的服务错误。"""


class ReimbursementStatusTool:
    """只读查询工具；数据库条件是最后一道资源归属边界。"""

    name = TOOL_NAME

    async def execute(
        self,
        session: AsyncSession,
        identity: AuthenticatedIdentity,
        *,
        reimbursement_number: str,
        request_id: UUID | None = None,
    ) -> ReimbursementStatusResult:
        arguments = ReimbursementStatusToolArguments(
            reimbursement_number=reimbursement_number
        )
        request_id = request_id or uuid4()
        statement = select(Reimbursement).where(
            Reimbursement.claim_number == arguments.reimbursement_number,
            # 这是越权防护的核心条件，不能由模型参数替代。
            Reimbursement.employee_id == identity.user_id,
        )
        with trace_stage("tool", tool_name=self.name) as tool_trace:
            try:
                result = await session.execute(statement)
                reimbursement = result.scalar_one_or_none()
                tool_trace["_outcome"] = "success" if reimbursement is not None else "not_found"
            except ReimbursementStatusToolError:
                raise
            except Exception as exc:
                await session.rollback()
                tool_trace["_outcome"] = "error"
                raise ReimbursementStatusToolError("Reimbursement status tool failed") from exc
            if reimbursement is None:
                await self._audit(
                    session,
                    request_id=request_id,
                    actor_id=identity.user_id,
                    outcome="not_found",
                    claim_number=arguments.reimbursement_number,
                )
                return ReimbursementStatusResult(
                    claim_number=arguments.reimbursement_number,
                    status="not_found",
                    message="未找到属于当前登录用户的报销单",
                )

            await self._audit(
                session,
                request_id=request_id,
                actor_id=identity.user_id,
                outcome="success",
                claim_number=arguments.reimbursement_number,
            )
            return ReimbursementStatusResult(
                claim_number=reimbursement.claim_number,
                status=reimbursement.status,
                message=(
                    f"报销单 {reimbursement.claim_number} 当前状态为：{reimbursement.current_step}"
                ),
                expense_date=reimbursement.expense_date,
                amount=float(reimbursement.amount),
                currency=reimbursement.currency,
                current_step=reimbursement.current_step,
                return_reason=reimbursement.return_reason,
            )

    async def _audit(
        self,
        session: AsyncSession,
        *,
        request_id: UUID,
        actor_id: UUID,
        outcome: str,
        claim_number: str,
    ) -> None:
        # 只记录单号摘要和结果，不把完整问题、金额或制度上下文写入审计表。
        session.add(
            ToolAudit(
                request_id=request_id,
                actor_id=actor_id,
                tool_name=self.name,
                outcome=outcome,
                input_summary={"reimbursement_number": claim_number},
            )
        )
        await session.commit()


def get_reimbursement_status_tool() -> ReimbursementStatusTool:
    return ReimbursementStatusTool()
