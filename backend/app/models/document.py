from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
    from app.models.document_chunk import DocumentChunk
    from app.models.knowledge_base import KnowledgeBase


class Document(Base):
    """上传到知识库的原始文件及其处理状态。"""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_documents_status",
        ),
        CheckConstraint(
            "text_extraction_status IN "
            "('not_attempted', 'complete', 'partial', 'no_text', 'failed')",
            name="ck_documents_text_extraction_status",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="ck_documents_effective_date_range",
        ),
        Index(
            "uq_documents_knowledge_base_sha256",
            "knowledge_base_id",
            "sha256",
            unique=True,
            postgresql_where=text("sha256 IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    version_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    access_scope: Mapped[str] = mapped_column(
        String(50), nullable=False, default="all_employees", server_default="all_employees"
    )
    supersedes_document_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    embedded_image_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    image_only_page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    text_extraction_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="not_attempted",
        server_default="not_attempted",
    )
    needs_ocr: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    parse_warnings: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'::json"),
    )
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="documents")
    supersedes: Mapped["Document | None"] = relationship(
        remote_side="Document.id",
        foreign_keys=[supersedes_document_id],
        back_populates="superseded_by",
    )
    superseded_by: Mapped[list["Document"]] = relationship(
        foreign_keys=[supersedes_document_id],
        back_populates="supersedes",
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
