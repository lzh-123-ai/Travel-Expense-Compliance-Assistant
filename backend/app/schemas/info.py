"""应用诊断元数据的 HTTP 响应数据结构。"""

from typing import Literal

from pydantic import BaseModel


class InfoResponse(BaseModel):
    """应用诊断信息响应。"""

    project: str
    stage: int
    features: list[str]
    # 只暴露当前路由模式，不暴露模型名称、密钥或任何用户数据。
    tool_routing_provider: Literal["rule", "bailian"]
