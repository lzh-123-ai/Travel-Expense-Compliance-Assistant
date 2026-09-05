"""助手接口的小规模延迟与并发趋势基准。

该基准不代表生产容量，报告不保存问题原文或回答正文。
脚本按当前 API 配置调用真实路由；单元测试只验证统计逻辑。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
from collections import Counter
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import PROJECT_ROOT

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "evaluation" / "results"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 90.0

# 固定三条代表性链路，只发送虚构报销场景，不携带真实员工或业务数据。
DEMO_CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "policy_rag",
        "question": "2026 年 5 月去上海参加大型展会，住宿费标准是多少？",
        "expense_date": "2026-05-10",
    },
    {
        "name": "reimbursement_status",
        "question": "我的报销单 BX-2026-0001 到哪一步了？",
        "expense_date": None,
    },
    {
        "name": "compliance_precheck",
        "question": "报销单 BX-2026-0001 是否符合住宿制度，能不能报销？",
        "expense_date": None,
    },
)


def percentile(values: list[float], fraction: float) -> float | None:
    """按 nearest-rank 计算百分位，避免小样本插值制造虚假精度。"""

    if not values:
        return None
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    ordered = sorted(values)
    return ordered[max(0, ceil(fraction * len(ordered)) - 1)]


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """把单次请求记录聚合成可比较的趋势摘要。"""

    latencies = [float(item["latency_ms"]) for item in samples]
    successful = [item for item in samples if item.get("status") == "ok"]
    return {
        "request_count": len(samples),
        "success_count": len(successful),
        "error_count": len(samples) - len(successful),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": round(percentile(latencies, 0.50), 3) if latencies else None,
            "p95": round(percentile(latencies, 0.95), 3) if latencies else None,
            "max": round(max(latencies), 3) if latencies else None,
        },
        "status_codes": dict(Counter(str(item.get("status_code")) for item in samples)),
        "routes": dict(Counter(item.get("route") for item in successful if item.get("route"))),
    }


def _post_json(
    *,
    url: str,
    payload: dict[str, Any],
    token: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    """发送一次 JSON 请求，只返回报告需要的安全字段。"""

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            response_payload = json.loads(body) if body else {}
            return {
                "status": "ok" if 200 <= response.status < 300 else "error",
                "status_code": response.status,
                "route": response_payload.get("route"),
                "request_id": response_payload.get("request_id")
                or response.headers.get("x-request-id"),
                "error": None,
            }
    except HTTPError as exc:
        return {
            "status": "error",
            "status_code": exc.code,
            "route": None,
            "request_id": exc.headers.get("x-request-id") if exc.headers else None,
            "error": "http_error",
        }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {
            "status": "error",
            "status_code": None,
            "route": None,
            "request_id": None,
            "error": "request_failed",
        }


async def _run_case(
    *,
    base_url: str,
    knowledge_base_id: str,
    case: dict[str, Any],
    token: str | None,
    timeout_seconds: float,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        started = perf_counter()
        result = await asyncio.to_thread(
            _post_json,
            url=f"{base_url.rstrip('/')}/api/v1/knowledge-bases/{knowledge_base_id}/assistant",
            payload={
                "question": case["question"],
                "expense_date": case["expense_date"],
                "top_k": 5,
            },
            token=token,
            timeout_seconds=timeout_seconds,
        )
        result["case"] = case["name"]
        result["latency_ms"] = round((perf_counter() - started) * 1000, 3)
        return result


async def run_benchmark(
    *,
    base_url: str,
    knowledge_base_id: str,
    concurrency_levels: list[int],
    repeat: int,
    token: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """按多个并发档位执行固定三条链路并返回 JSON 可序列化报告。"""

    if not knowledge_base_id.strip():
        raise ValueError("knowledge_base_id is required")
    if repeat < 1 or any(level < 1 for level in concurrency_levels):
        raise ValueError("repeat and concurrency levels must be at least one")

    levels: list[dict[str, Any]] = []
    for level in concurrency_levels:
        semaphore = asyncio.Semaphore(level)
        tasks = [
            _run_case(
                base_url=base_url,
                knowledge_base_id=knowledge_base_id,
                case=case,
                token=token,
                timeout_seconds=timeout_seconds,
                semaphore=semaphore,
            )
            for _ in range(repeat)
            for case in DEMO_CASES
        ]
        samples = await asyncio.gather(*tasks)
        levels.append(
            {
                "concurrency": level,
                "summary": summarize_samples(samples),
                "samples": samples,
            }
        )

    return {
        "benchmark_version": "assistant-latency-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "knowledge_base_id": knowledge_base_id,
        "repeat_per_case": repeat,
        "case_names": [case["name"] for case in DEMO_CASES],
        "levels": levels,
        "interpretation": (
            "小样本本地趋势记录，不代表生产 SLA；模型、数据库和机器变化都会影响结果。"
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行小规模助手延迟与并发基准")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 2])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    report = await run_benchmark(
        base_url=args.base_url,
        knowledge_base_id=args.knowledge_base_id,
        concurrency_levels=args.concurrency,
        repeat=args.repeat,
        token=os.getenv("BENCHMARK_JWT"),
        timeout_seconds=args.timeout_seconds,
    )
    output_path = args.output or (
        DEFAULT_OUTPUT_DIR
        / f"assistant_benchmark_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(output_path), "levels": [item["summary"] for item in report["levels"]]},
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
