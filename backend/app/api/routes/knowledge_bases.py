from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseResponse

router = APIRouter(prefix="/knowledge-bases")
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get(
    "",
    response_model=list[KnowledgeBaseResponse],
    summary="List knowledge bases",
)
async def list_knowledge_bases(session: DatabaseSession) -> list[KnowledgeBase]:
    statement = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
    result = await session.execute(statement)
    return list(result.scalars().all())


@router.get(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
    summary="Get a knowledge base",
)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    session: DatabaseSession,
) -> KnowledgeBase:
    knowledge_base = await session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )

    return knowledge_base


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
