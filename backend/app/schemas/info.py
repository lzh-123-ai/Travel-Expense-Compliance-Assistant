"""应用诊断元数据的 HTTP 响应数据结构。"""

from pydantic import BaseModel


class InfoResponse(BaseModel):
    """info接口的响应结构，也就是后端对前端作出的数据承诺。"""

    project: str
    stage: int
    features: list[str]
