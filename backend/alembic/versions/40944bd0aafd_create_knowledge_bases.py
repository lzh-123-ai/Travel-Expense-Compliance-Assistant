"""create knowledge bases

Revision ID: 40944bd0aafd
Revises:
Create Date: 2026-07-23 21:48:04.996789
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "40944bd0aafd"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    # learning_check 是数据库中的遗留表，不属于应用 ORM 管理范围。


def downgrade() -> None:
    op.drop_table("knowledge_bases")
