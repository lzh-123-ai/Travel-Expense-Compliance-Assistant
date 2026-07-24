from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseCreate(BaseModel):
    """创建知识库时，客户端允许提交的数据。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class KnowledgeBaseResponse(BaseModel):
    """创建成功后，API 返回给客户端的完整知识库数据。"""

    model_config = ConfigDict(from_attributes=True)  # 允许从 ORM 对象读取

    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
