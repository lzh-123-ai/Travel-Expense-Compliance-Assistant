from fastapi import APIRouter

from app.api.routes import documents, health, info, knowledge_bases, ready

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(info.router, tags=["system"])
api_router.include_router(ready.router, tags=["system"])
api_router.include_router(knowledge_bases.router, tags=["knowledge-bases"])
api_router.include_router(documents.router, tags=["documents"])
