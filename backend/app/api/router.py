"""HTTP 路由注册表，也是从 ``main.py`` 继续定位的第一站。

每个被挂载模块负责一个业务面。Route 编排 HTTP 请求；Service 承担可复用的
业务规则、存储操作和模型适配。
"""

from fastapi import APIRouter

from app.api.routes import answers, documents, health, info, knowledge_bases, ready, retrieval

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(info.router, tags=["system"])
api_router.include_router(ready.router, tags=["system"])
api_router.include_router(knowledge_bases.router, tags=["knowledge-bases"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(retrieval.router, tags=["retrieval"])
api_router.include_router(answers.router, tags=["answers"])
