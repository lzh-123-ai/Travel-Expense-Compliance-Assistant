"""可追溯派生文本切片及其检索数据的 ORM 记录。

向量和关键词字段都是可选的派生数据。内容哈希保证解析、模型或分词器变化后，
增量索引仍然正确。
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document import Document


class DocumentChunk(Base):
    """可追溯到原文位置并支持增量索引的确定性文本切片。"""

    def __init__(self, **kwargs: object) -> None:
        """在 ORM 实例化阶段补齐数据库默认值。"""
        super().__init__(**kwargs)
        if self.source_metadata is None:
            self.source_metadata = {}

    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_document_chunks_ordinal"),
        CheckConstraint("char_count > 0", name="ck_document_chunks_char_count"),
        CheckConstraint("token_estimate > 0", name="ck_document_chunks_token_estimate"),
        CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_document_chunks_page_range",
        ),
        Index(
            "uq_document_chunks_document_ordinal",
            "document_id",
            "ordinal",
            unique=True,
        ),
        Index(
            "ix_document_chunks_embedding_model",
            "embedding_provider",
            "embedding_model",
        ),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_document_chunks_keyword_search_gin",
            text("to_tsvector('simple', keyword_search_text)"),
            postgresql_using="gin",
        ),
        CheckConstraint(
            "(embedding IS NULL AND embedding_provider IS NULL AND embedding_model IS NULL "
            "AND embedding_dimension IS NULL AND embedding_content_hash IS NULL "
            "AND embedded_at IS NULL) OR "
            "(embedding IS NOT NULL AND embedding_provider IS NOT NULL "
            "AND embedding_model IS NOT NULL AND embedding_dimension = 512 "
            "AND embedding_content_hash IS NOT NULL AND embedded_at IS NOT NULL)",
            name="ck_document_chunks_embedding_metadata",
        ),
        CheckConstraint(
            "(keyword_search_text IS NULL AND keyword_tokenizer IS NULL "
            "AND keyword_content_hash IS NULL AND keyword_indexed_at IS NULL) OR "
            "(keyword_search_text IS NOT NULL AND keyword_tokenizer IS NOT NULL "
            "AND keyword_content_hash IS NOT NULL AND keyword_indexed_at IS NOT NULL)",
            name="ck_document_chunks_keyword_metadata",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(30), nullable=False)
    # 记录 OCR provider、模型版本、页码、行置信度和框坐标等，不参与检索过滤。
    source_metadata: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    chunking_strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    keyword_search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyword_tokenizer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    keyword_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    keyword_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")
