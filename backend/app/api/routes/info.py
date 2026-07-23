from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.info import InfoResponse

router = APIRouter()


@router.get(
    "/info",
    response_model=InfoResponse,
    summary="获取项目信息",
)
async def info_check() -> InfoResponse:
    settings = get_settings()
    return InfoResponse(
        project=settings.app_name,
        stage=3,
        features=["health-check", "postgres-pgvector", "database-readiness"],
    )
