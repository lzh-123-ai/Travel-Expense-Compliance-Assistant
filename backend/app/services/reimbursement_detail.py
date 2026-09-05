"""本人报销明细只读工具。

合规预检查需要费用日期和金额等业务事实，但这些事实仍然必须通过服务端身份
过滤后才能读取。工具不接受 ``user_id``、SQL 或模型生成的任意筛选条件。
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
from app.services.tool_contracts import ReimbursementDetailToolArguments

TOOL_NAME = "get_my_reimbursement_detail"


@dataclass(frozen=True)
class ReimbursementDetailResult:
    """脱离 ORM 的安全业务事实，避免把数据库对象直接交给模型或 API。"""

    claim_number: str
    expense_date: date | None
    amount: float | None
    currency: str | None
    status: str
    current_step: str | None
    return_reason: str | None
    message: str


class ReimbursementDetailToolError(RuntimeError):
    """明细查询失败；上层应保留失败分支，不伪造合规结论。"""


class ReimbursementDetailTool:
    """读取本人报销事实，并记录最小化工具审计。"""

    name = TOOL_NAME

    async def execute(
        self,
        session: AsyncSession,
        identity: AuthenticatedIdentity,
        *,
        reimbursement_number: str,
        request_id: UUID | None = None,
    ) -> ReimbursementDetailResult:
        arguments = ReimbursementDetailToolArguments(
            reimbursement_number=reimbursement_number
        )
        request_id = request_id or uuid4()
        statement = select(Reimbursement).where(
            Reimbursement.claim_number == arguments.reimbursement_number,
            # 资源归属必须落在 SQL 条件中，不能靠 Prompt 或图节点补救。
            Reimbursement.employee_id == identity.user_id,
        )
        with trace_stage("tool", tool_name=self.name) as tool_trace:
            try:
                result = await session.execute(statement)
                reimbursement = result.scalar_one_or_none()
                tool_trace["_outcome"] = "success" if reimbursement is not None else "not_found"
            except ReimbursementDetailToolError:
                raise
            except Exception as exc:
                await session.rollback()
                tool_trace["_outcome"] = "error"
                raise ReimbursementDetailToolError("Reimbursement detail tool failed") from exc
            if reimbursement is None:
                await self._audit(
                    session,
                    request_id=request_id,
                    actor_id=identity.user_id,
                    outcome="not_found",
                    claim_number=arguments.reimbursement_number,
                )
                return ReimbursementDetailResult(
                    claim_number=arguments.reimbursement_number,
                    expense_date=None,
                    amount=None,
                    currency=None,
                    status="not_found",
                    current_step=None,
                    return_reason=None,
                    message="未找到属于当前登录用户的报销单，无法进行合规预检查。",
                )

            await self._audit(
                session,
                request_id=request_id,
                actor_id=identity.user_id,
                outcome="success",
                claim_number=arguments.reimbursement_number,
            )
            return ReimbursementDetailResult(
                claim_number=reimbursement.claim_number,
                expense_date=reimbursement.expense_date,
                amount=float(reimbursement.amount),
                currency=reimbursement.currency,
                status=reimbursement.status,
                current_step=reimbursement.current_step,
                return_reason=reimbursement.return_reason,
                message=f"已读取报销单 {reimbursement.claim_number} 的本人业务事实。",
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
        # 审计只保留单号和结果，不记录金额、完整问题或制度上下文。
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


def get_reimbursement_detail_tool() -> ReimbursementDetailTool:
    return ReimbursementDetailTool()
