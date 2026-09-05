"""依赖就绪诊断的 HTTP 响应数据结构。"""

from typing import Literal

from pydantic import BaseModel


class ReadinessResponse(BaseModel):
    """处理业务请求所需依赖的就绪状态。"""

    status: Literal["ready"]
    database: Literal["ok"]
