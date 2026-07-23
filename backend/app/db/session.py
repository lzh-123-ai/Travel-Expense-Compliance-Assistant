from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,  # 启用连接池预检查，防止使用失效连接
)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 提交后不自动过期数据，便于后续使用
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """一个请求分配一个数据库会话，请求处理完毕后务必关闭该会话。"""
    async with async_session_factory() as session:
        yield session


async def close_database_connections() -> None:
    """应用程序关闭时，释放数据库连接池中的所有连接。"""
    await engine.dispose()
