"""add deterministic keyword search fields

Revision ID: d9f2a6b7c8e1
Revises: c8e4b7a1d2f0
Create Date: 2026-08-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9f2a6b7c8e1"
down_revision: str | Sequence[str] | None = "c8e4b7a1d2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("keyword_search_text", sa.Text(), nullable=True))
    op.add_column(
        "document_chunks", sa.Column("keyword_tokenizer", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "document_chunks", sa.Column("keyword_content_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "document_chunks",
        sa.Column("keyword_indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_document_chunks_keyword_metadata",
        "document_chunks",
        "(keyword_search_text IS NULL AND keyword_tokenizer IS NULL "
        "AND keyword_content_hash IS NULL AND keyword_indexed_at IS NULL) OR "
        "(keyword_search_text IS NOT NULL AND keyword_tokenizer IS NOT NULL "
        "AND keyword_content_hash IS NOT NULL AND keyword_indexed_at IS NOT NULL)",
    )
    op.create_index(
        "ix_document_chunks_keyword_search_gin",
        "document_chunks",
        [sa.text("to_tsvector('simple', keyword_search_text)")],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_keyword_search_gin", table_name="document_chunks")
    op.drop_constraint("ck_document_chunks_keyword_metadata", "document_chunks", type_="check")
    op.drop_column("document_chunks", "keyword_indexed_at")
    op.drop_column("document_chunks", "keyword_content_hash")
    op.drop_column("document_chunks", "keyword_tokenizer")
    op.drop_column("document_chunks", "keyword_search_text")
