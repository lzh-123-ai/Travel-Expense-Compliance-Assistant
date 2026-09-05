"""新增报销状态工具与审计表

Revision ID: 8d5f2c1a7e90
Revises: d9f2a6b7c8e1
Create Date: 2026-08-15 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8d5f2c1a7e90"
down_revision: str | Sequence[str] | None = "d9f2a6b7c8e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建只读状态查询的事实表和最小工具审计表。"""
    op.create_table(
        "reimbursements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("claim_number", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("current_step", sa.String(length=80), nullable=False),
        sa.Column("return_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'under_review', 'approved', 'returned', 'paid')",
            name="ck_reimbursements_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_number"),
    )
    op.create_index("ix_reimbursements_claim_number", "reimbursements", ["claim_number"])
    op.create_index("ix_reimbursements_employee_id", "reimbursements", ["employee_id"])

    op.create_table(
        "tool_audits",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_audits_request_id", "tool_audits", ["request_id"])
    op.create_index("ix_tool_audits_actor_id", "tool_audits", ["actor_id"])


def downgrade() -> None:
    """按外键依赖顺序删除工具审计表和报销事实表。"""
    op.drop_index("ix_tool_audits_actor_id", table_name="tool_audits")
    op.drop_index("ix_tool_audits_request_id", table_name="tool_audits")
    op.drop_table("tool_audits")
    op.drop_index("ix_reimbursements_employee_id", table_name="reimbursements")
    op.drop_index("ix_reimbursements_claim_number", table_name="reimbursements")
    op.drop_table("reimbursements")
