"""FastAPI 应用组装、路由挂载和进程生命周期管理。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import UUID

from fastapi import FastAPI
from starlette.requests import Request

from app.api.router import api_router
from app.core.config import get_settings
from app.core.observability import (
    bind_trace,
    log_trace,
    new_request_trace,
    observability_store,
    reset_trace,
)
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

    @application.middleware("http")
    async def request_observability(request: Request, call_next):
        """统一生成 request_id，并把一次请求的阶段轨迹交给内存观测存储。"""
        raw_request_id = request.headers.get("X-Request-ID")
        try:
            request_id = UUID(raw_request_id) if raw_request_id else None
        except ValueError:
            request_id = None
        trace = new_request_trace(request.method, request.url.path, request_id)
        request.state.request_id = trace.request_id
        token = bind_trace(trace)
        response = None
        started = perf_counter()
        try:
            response = await call_next(request)
            trace.record("request", status_code=response.status_code)
            return response
        except Exception as exc:
            trace.record("request", outcome="error", status_code=500, error_type=type(exc).__name__)
            raise
        finally:
            trace.status_code = response.status_code if response is not None else 500
            trace.total_latency_ms = (perf_counter() - started) * 1000
            observability_store.record(trace)
            log_trace(trace)
            if response is not None:
                response.headers["X-Request-ID"] = str(trace.request_id)
            reset_trace(token)

    return application


app = create_app()
