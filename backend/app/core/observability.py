"""轻量请求观测：按 request_id 聚合有限数量的近期阶段轨迹。

生产部署可将相同结构化记录转发到持久化日志或观测平台。
"""

from __future__ import annotations

import json
import logging
from collections import Counter, deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class TraceEvent:
    """单个服务阶段的安全诊断信息，不保存问题正文或制度内容。"""

    stage: str
    outcome: str
    latency_ms: float
    fields: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "outcome": self.outcome,
            "latency_ms": round(self.latency_ms, 3),
            **self.fields,
        }


@dataclass
class RequestTrace:
    """一次 HTTP 请求的有限轨迹；只存可用于排错的元数据。"""

    request_id: UUID
    method: str
    path: str
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    events: list[TraceEvent] = field(default_factory=list)
    status_code: int | None = None
    total_latency_ms: float | None = None

    def record(
        self,
        stage: str,
        *,
        outcome: str = "success",
        latency_ms: float = 0.0,
        **fields: Any,
    ) -> None:
        self.events.append(
            TraceEvent(stage=stage, outcome=outcome, latency_ms=latency_ms, fields=fields)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": str(self.request_id),
            "method": self.method,
            "path": self.path,
            "started_at": self.started_at,
            "status_code": self.status_code,
            "total_latency_ms": round(self.total_latency_ms or 0.0, 3),
            "events": [event.as_dict() for event in self.events],
        }


class ObservabilityStore:
    """保存最近轨迹并维护按阶段聚合计数，避免引入外部监控组件。"""

    def __init__(self, max_traces: int = 200) -> None:
        self._traces: deque[RequestTrace] = deque(maxlen=max_traces)
        self._request_count = 0
        self._error_count = 0
        self._stage_counts: Counter[str] = Counter()
        self._stage_errors: Counter[str] = Counter()
        self._stage_latency: dict[str, list[float]] = {}

    def record(self, trace: RequestTrace) -> None:
        self._traces.append(trace)
        self._request_count += 1
        if (trace.status_code or 500) >= 500:
            self._error_count += 1
        for event in trace.events:
            self._stage_counts[event.stage] += 1
            self._stage_latency.setdefault(event.stage, []).append(event.latency_ms)
            if event.outcome != "success":
                self._stage_errors[event.stage] += 1

    def get(self, request_id: UUID) -> dict[str, Any] | None:
        for trace in reversed(self._traces):
            if trace.request_id == request_id:
                return trace.as_dict()
        return None

    def snapshot(self) -> dict[str, Any]:
        stages: dict[str, Any] = {}
        for stage, count in self._stage_counts.items():
            latencies = self._stage_latency.get(stage, [])
            stages[stage] = {
                "count": count,
                "errors": self._stage_errors.get(stage, 0),
                "avg_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            }
        return {
            "requests": {
                "total": self._request_count,
                "errors": self._error_count,
            },
            "stages": stages,
            "retained_traces": len(self._traces),
        }


_current_trace: ContextVar[RequestTrace | None] = ContextVar("current_request_trace", default=None)
observability_store = ObservabilityStore()


def new_request_trace(method: str, path: str, request_id: UUID | None = None) -> RequestTrace:
    return RequestTrace(request_id=request_id or uuid4(), method=method, path=path)


def bind_trace(trace: RequestTrace) -> Token[RequestTrace | None]:
    return _current_trace.set(trace)


def reset_trace(token: Token[RequestTrace | None]) -> None:
    _current_trace.reset(token)


def current_trace() -> RequestTrace | None:
    return _current_trace.get()


def record_stage(
    stage: str,
    *,
    outcome: str = "success",
    latency_ms: float = 0.0,
    **fields: Any,
) -> None:
    """在有 HTTP 请求上下文时记录阶段；离线评测和单测调用时安全地忽略。"""
    trace = current_trace()
    if trace is not None:
        trace.record(stage, outcome=outcome, latency_ms=latency_ms, **fields)


@contextmanager
def trace_stage(stage: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """包裹一个阶段，自动记录耗时和异常类型，调用方可补充结果字段。"""
    started = perf_counter()
    metadata = fields
    try:
        yield metadata
    except Exception as exc:
        metadata.setdefault("error_type", type(exc).__name__)
        record_stage(
            stage,
            outcome="error",
            latency_ms=(perf_counter() - started) * 1000,
            **metadata,
        )
        raise
    else:
        record_stage(
            stage,
            outcome=metadata.pop("_outcome", "success"),
            latency_ms=(perf_counter() - started) * 1000,
            **metadata,
        )


def read_token_usage(response: Any) -> tuple[int | None, int | None]:
    """兼容 LangChain 两种 usage 形状，统一成输入/输出 token。"""
    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    token_usage = getattr(response, "response_metadata", {}).get("token_usage", {})
    return (
        input_tokens if input_tokens is not None else token_usage.get("prompt_tokens"),
        output_tokens if output_tokens is not None else token_usage.get("completion_tokens"),
    )


def estimated_model_cost(input_tokens: int | None, output_tokens: int | None) -> float | None:
    """按配置的每百万 token 价格估算费用；价格为 0 时明确记录 0 而非猜价格。"""
    if input_tokens is None and output_tokens is None:
        return None
    settings = get_settings()
    return round(
        (input_tokens or 0) / 1_000_000 * settings.answer_input_cost_per_million_usd
        + (output_tokens or 0) / 1_000_000 * settings.answer_output_cost_per_million_usd,
        8,
    )


def log_trace(trace: RequestTrace) -> None:
    """输出一行 JSON 结构化日志，便于按 request_id 检索。"""
    logger.info("request_trace %s", json.dumps(trace.as_dict(), ensure_ascii=False, default=str))
