"""存活检查接口的 HTTP 响应数据结构。"""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """健康检查接口的响应结构，也就是后端对前端作出的数据承诺。"""

    status: Literal["ok"]
    app_name: str
    environment: str
