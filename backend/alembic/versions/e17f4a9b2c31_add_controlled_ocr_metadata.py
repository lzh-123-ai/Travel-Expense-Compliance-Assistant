"""add controlled OCR metadata and chunk provenance

Revision ID: e17f4a9b2c31
Revises: 8d5f2c1a7e90
Create Date: 2026-08-31 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e17f4a9b2c31"
down_revision: str | Sequence[str] | None = "8d5f2c1a7e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记录 OCR 后处理状态，并为每个切片增加识别溯源 JSON。"""
    op.add_column(
        "documents",
        sa.Column(
            "ocr_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_requested",
        ),
    )
    op.add_column("documents", sa.Column("ocr_provider", sa.String(length=50), nullable=True))
    op.add_column(
        "documents", sa.Column("ocr_model_version", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("ocr_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "ocr_low_confidence_page_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_documents_ocr_status",
        "documents",
        "ocr_status IN ('not_requested', 'pending', 'completed', 'partial', 'failed')",
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "source_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    """撤销 OCR 字段；已有切片正文和原生解析字段不受影响。"""
    op.drop_column("document_chunks", "source_metadata")
    op.drop_constraint("ck_documents_ocr_status", "documents", type_="check")
    op.drop_column("documents", "ocr_low_confidence_page_count")
    op.drop_column("documents", "ocr_processed_at")
    op.drop_column("documents", "ocr_model_version")
    op.drop_column("documents", "ocr_provider")
    op.drop_column("documents", "ocr_status")
