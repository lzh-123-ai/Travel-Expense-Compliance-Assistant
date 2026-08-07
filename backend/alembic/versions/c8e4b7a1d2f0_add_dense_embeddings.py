"""add dense embeddings

Revision ID: c8e4b7a1d2f0
Revises: f3a1c9d7e842
Create Date: 2026-08-04 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "c8e4b7a1d2f0"
down_revision: str | Sequence[str] | None = "f3a1c9d7e842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("document_chunks", sa.Column("embedding", Vector(512), nullable=True))
    op.add_column(
        "document_chunks", sa.Column("embedding_provider", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "document_chunks", sa.Column("embedding_model", sa.String(length=200), nullable=True)
    )
    op.add_column("document_chunks", sa.Column("embedding_dimension", sa.Integer(), nullable=True))
    op.add_column(
        "document_chunks",
        sa.Column("embedding_content_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "document_chunks", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_document_chunks_embedding_metadata",
        "document_chunks",
        "(embedding IS NULL AND embedding_provider IS NULL AND embedding_model IS NULL "
        "AND embedding_dimension IS NULL AND embedding_content_hash IS NULL "
        "AND embedded_at IS NULL) OR "
        "(embedding IS NOT NULL AND embedding_provider IS NOT NULL "
        "AND embedding_model IS NOT NULL AND embedding_dimension = 512 "
        "AND embedding_content_hash IS NOT NULL AND embedded_at IS NOT NULL)",
    )
    op.create_index(
        "ix_document_chunks_embedding_model",
        "document_chunks",
        ["embedding_provider", "embedding_model"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding_hnsw", table_name="document_chunks")
    op.drop_index("ix_document_chunks_embedding_model", table_name="document_chunks")
    op.drop_constraint("ck_document_chunks_embedding_metadata", "document_chunks", type_="check")
    op.drop_column("document_chunks", "embedded_at")
    op.drop_column("document_chunks", "embedding_content_hash")
    op.drop_column("document_chunks", "embedding_dimension")
    op.drop_column("document_chunks", "embedding_model")
    op.drop_column("document_chunks", "embedding_provider")
    op.drop_column("document_chunks", "embedding")
