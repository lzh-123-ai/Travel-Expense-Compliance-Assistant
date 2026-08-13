"""就绪检查接口：确认接受真实请求所需的依赖是否可用。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.ready import ReadinessResponse

router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Check whether required dependencies are ready",
)
async def readiness_check(session: DatabaseSession) -> ReadinessResponse:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc

    return ReadinessResponse(status="ready", database="ok")
