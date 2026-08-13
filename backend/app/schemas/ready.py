"""依赖就绪诊断的 HTTP 响应数据结构。"""

from typing import Literal

from pydantic import BaseModel


class ReadinessResponse(BaseModel):
    """Readiness state for dependencies required to serve real traffic."""

    status: Literal["ready"]
    database: Literal["ok"]
