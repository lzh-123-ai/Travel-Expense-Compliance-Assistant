"""只读报销状态与合规预检查使用的业务事实记录。"""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Reimbursement(Base):
    """报销单事实表；``employee_id`` 是工具查询的服务端归属边界。"""

    __tablename__ = "reimbursements"
    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted', 'under_review', 'approved', 'returned', 'paid')",
            name="ck_reimbursements_status",
        ),
        UniqueConstraint("claim_number"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False, index=True
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="CNY", server_default="CNY"
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    current_step: Mapped[str] = mapped_column(String(80), nullable=False)
    return_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
