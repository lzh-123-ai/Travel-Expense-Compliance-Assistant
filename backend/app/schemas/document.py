"""文档生命周期和切片查看的 HTTP 请求/响应数据结构。"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentResponse(BaseModel):
    """客户端可见的文档元数据，不暴露服务器内部存储路径。"""

    # SQLAlchemy ORM 对象不是字典，因此允许 Pydantic 从对象属性读取字段。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    original_filename: str
    content_type: str
    file_size: int
    sha256: str | None
    status: str
    error_message: str | None
    policy_type: str | None
    version_label: str | None
    effective_from: date | None
    effective_to: date | None
    access_scope: str
    supersedes_document_id: UUID | None
    embedded_image_count: int
    image_only_page_count: int
    text_extraction_status: str
    needs_ocr: bool
    parse_warnings: list[dict[str, object]]
    parsed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentMetadataUpdate(BaseModel):
    """可人工确认的领域元数据；解析器不会从文件名擅自推断这些值。"""

    policy_type: Annotated[str, Field(max_length=50)] | None = None
    version_label: Annotated[str, Field(max_length=50)] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    access_scope: Literal["all_employees", "finance_only"] = "all_employees"
    supersedes_document_id: UUID | None = None

    @model_validator(mode="after")
    def validate_effective_range(self) -> "DocumentMetadataUpdate":
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must not be earlier than effective_from")
        return self


class DocumentChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    ordinal: int
    content: str
    page_start: int | None
    page_end: int | None
    section_path: list[str]
    char_count: int
    token_estimate: int
    content_hash: str
    extraction_method: str
    chunking_strategy: str
    chunk_size: int
    chunk_overlap: int
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dimension: int | None
    embedding_content_hash: str | None
    embedded_at: datetime | None
    created_at: datetime
