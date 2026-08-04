"""harden document ingestion

Revision ID: bd1fd867ba63
Revises: 07c25444327d
Create Date: 2026-07-26 18:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bd1fd867ba63"
down_revision: str | Sequence[str] | None = "07c25444327d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 旧版本只把文件放在 uploads 根目录，因此保留最后一段作为相对 storage key。
    # 历史文件无法由数据库迁移可靠读取并计算哈希，sha256 暂时允许为空；
    # 所有新上传都由应用层写入哈希，并受下面的部分唯一索引保护。
    op.execute(
        sa.text(
            "UPDATE documents SET storage_path = regexp_replace(storage_path, '^.*[\\\\/]', '')"
        )
    )
    op.alter_column("documents", "storage_path", new_column_name="storage_key")
    op.add_column("documents", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_documents_knowledge_base_sha256",
        "documents",
        ["knowledge_base_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_knowledge_base_sha256", table_name="documents")
    op.drop_column("documents", "sha256")
    op.alter_column("documents", "storage_key", new_column_name="storage_path")
