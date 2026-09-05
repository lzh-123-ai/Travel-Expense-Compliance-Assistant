"""存活检查接口的 HTTP 响应数据结构。"""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """存活检查响应。"""

    status: Literal["ok"]
    app_name: str
    environment: str
