from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
    created_at: datetime
    updated_at: datetime
