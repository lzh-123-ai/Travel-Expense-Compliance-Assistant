"""集中注册各业务模块的 HTTP 路由。"""

from fastapi import APIRouter

from app.api.routes import (
    answers,
    assistant,
    documents,
    health,
    info,
    knowledge_bases,
    observability,
    ready,
    retrieval,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(info.router, tags=["system"])
api_router.include_router(ready.router, tags=["system"])
api_router.include_router(knowledge_bases.router, tags=["knowledge-bases"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(retrieval.router, tags=["retrieval"])
api_router.include_router(answers.router, tags=["answers"])
api_router.include_router(assistant.router, tags=["assistant"])
api_router.include_router(observability.router, tags=["observability"])
