from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseResponse

router = APIRouter(prefix="/knowledge-bases")
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.post(
    "",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge base",
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    session: DatabaseSession,
) -> KnowledgeBase:
    knowledge_base = KnowledgeBase(
        name=payload.name,
        description=payload.description,
    )
    session.add(knowledge_base)

    try:
        await session.commit()
    except IntegrityError as exc:
        # PostgreSQL 拒绝重复名称后，事务会进入失败状态；必须先回滚，
        # 这个 Session 才能继续执行其他数据库操作。
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Knowledge base name already exists",
        ) from exc

    # commit 后重新读取数据库生成的时间字段，确保响应内容完整。
    await session.refresh(knowledge_base)
    return knowledge_base
