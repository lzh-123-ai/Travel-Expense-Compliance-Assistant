"""add parsing metadata and chunks

Revision ID: f3a1c9d7e842
Revises: bd1fd867ba63
Create Date: 2026-07-30 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a1c9d7e842"
down_revision: str | Sequence[str] | None = "bd1fd867ba63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("policy_type", sa.String(length=50), nullable=True))
    op.add_column("documents", sa.Column("version_label", sa.String(length=50), nullable=True))
    op.add_column("documents", sa.Column("effective_from", sa.Date(), nullable=True))
    op.add_column("documents", sa.Column("effective_to", sa.Date(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "access_scope",
            sa.String(length=50),
            server_default="all_employees",
            nullable=False,
        ),
    )
    op.add_column("documents", sa.Column("supersedes_document_id", sa.UUID(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("embedded_image_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column("image_only_page_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column(
            "text_extraction_status",
            sa.String(length=20),
            server_default="not_attempted",
            nullable=False,
        ),
    )
    op.add_column(
        "documents", sa.Column("needs_ocr", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column(
        "documents",
        sa.Column(
            "parse_warnings", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
    )
    op.add_column("documents", sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_documents_text_extraction_status",
        "documents",
        "text_extraction_status IN ('not_attempted', 'complete', 'partial', 'no_text', 'failed')",
    )
    op.create_check_constraint(
        "ck_documents_effective_date_range",
        "documents",
        "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
    )
    op.create_foreign_key(
        "fk_documents_supersedes_document_id_documents",
        "documents",
        "documents",
        ["supersedes_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_documents_supersedes_document_id"),
        "documents",
        ["supersedes_document_id"],
        unique=False,
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("section_path", sa.JSON(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("extraction_method", sa.String(length=30), nullable=False),
        sa.Column("chunking_strategy", sa.String(length=50), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("char_count > 0", name="ck_document_chunks_char_count"),
        sa.CheckConstraint("ordinal >= 0", name="ck_document_chunks_ordinal"),
        sa.CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_document_chunks_page_range",
        ),
        sa.CheckConstraint("token_estimate > 0", name="ck_document_chunks_token_estimate"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_document_chunks_document_id"),
        "document_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "uq_document_chunks_document_ordinal",
        "document_chunks",
        ["document_id", "ordinal"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_document_chunks_document_ordinal", table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_document_id"), table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index(op.f("ix_documents_supersedes_document_id"), table_name="documents")
    op.drop_constraint(
        "fk_documents_supersedes_document_id_documents", "documents", type_="foreignkey"
    )
    op.drop_constraint("ck_documents_effective_date_range", "documents", type_="check")
    op.drop_constraint("ck_documents_text_extraction_status", "documents", type_="check")
    op.drop_column("documents", "parsed_at")
    op.drop_column("documents", "parse_warnings")
    op.drop_column("documents", "needs_ocr")
    op.drop_column("documents", "text_extraction_status")
    op.drop_column("documents", "image_only_page_count")
    op.drop_column("documents", "embedded_image_count")
    op.drop_column("documents", "supersedes_document_id")
    op.drop_column("documents", "access_scope")
    op.drop_column("documents", "effective_to")
    op.drop_column("documents", "effective_from")
    op.drop_column("documents", "version_label")
    op.drop_column("documents", "policy_type")
