"""应用组装入口。

从这里看 HTTP 请求如何进入项目：创建 FastAPI、挂载版本化路由，并在
进程结束时关闭数据库资源。具体业务入口继续查看 ``api/router.py``。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.db.session import close_database_connections


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """管理进程生命周期资源，并在服务关闭时释放连接。"""
    try:
        yield
    finally:
        await close_database_connections()


def create_app() -> FastAPI:
    """为生产运行和 API 测试创建独立的应用实例。"""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
        version="0.1.0",
        lifespan=lifespan,
    )

    application.include_router(
        api_router,
        prefix=settings.api_v1_prefix,
    )
    return application


app = create_app()
