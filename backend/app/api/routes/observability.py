"""可观测性诊断接口：只返回请求元数据、阶段耗时和聚合计数。"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.observability import observability_store

router = APIRouter(prefix="/observability")


@router.get("/metrics", summary="Read in-process request metrics")
async def metrics() -> dict[str, object]:
    """返回本进程的聚合指标；不包含问题正文、身份或制度内容。"""
    return observability_store.snapshot()


@router.get("/requests/{request_id}", summary="Read a request trace")
async def request_trace(request_id: UUID) -> dict[str, object]:
    """按 request_id 查询最近保留的调用轨迹，供 UI 和故障定位使用。"""
    trace = observability_store.get(request_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request trace not found")
    return trace
