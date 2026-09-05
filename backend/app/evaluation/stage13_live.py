"""百炼 Function Calling 联调脚本。

该脚本只验证“模型选择哪个能力、提出什么单号参数”，不执行数据库工具。
它与常规 pytest 分离，避免测试套件隐式消耗外部模型额度。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.evaluation.loader import load_eval_dataset
from app.services.bailian_tool_provider import (
    BailianIntentClassifier,
    ToolRoutingProviderError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_PATHS = (
    PROJECT_ROOT / "data" / "evaluation" / "stage12_tool_routing_v0.json",
    PROJECT_ROOT / "data" / "evaluation" / "stage13_compliance_v0.json",
)
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "results" / "stage13_tool_routing_live.json"
)


class _RecordingModel:
    """在不改变生产分类器的前提下，记录百炼响应元数据。"""

    def __init__(self, model: Any) -> None:
        self.model = model
        self.last_response: Any = None

    async def ainvoke(self, messages: list[Any]) -> Any:
        response = await self.model.ainvoke(messages)
        self.last_response = response
        return response


def _usage(response: Any) -> dict[str, int | None]:
    """兼容 LangChain 的 usage_metadata 和 response_metadata 两种返回形态。"""

    usage = getattr(response, "usage_metadata", None) or {}
    metadata = getattr(response, "response_metadata", None) or {}
    token_usage = metadata.get("token_usage") or metadata.get("usage") or {}
    return {
        "input_tokens": usage.get("input_tokens", token_usage.get("prompt_tokens")),
        "output_tokens": usage.get("output_tokens", token_usage.get("completion_tokens")),
        "total_tokens": usage.get("total_tokens", token_usage.get("total_tokens")),
    }


def _error_kind(exc: Exception) -> str:
    message = str(exc)
    if "does not match" in message or "not present" in message:
        return "claim_number_mismatch"
    if "invalid tool arguments" in message:
        return "unauthorized_or_invalid_arguments"
    if "multiple tools" in message:
        return "multiple_tools"
    if "Unknown tool" in message:
        return "unknown_tool"
    if "request failed" in message:
        return "provider_request_failed"
    return "provider_error"


def _model_tool_name(response: Any) -> str | None:
    calls = list(getattr(response, "tool_calls", None) or [])
    if not calls:
        return None
    call = calls[0]
    return call.get("name") if isinstance(call, dict) else getattr(call, "name", None)


def _model_argument_keys(response: Any) -> list[str]:
    calls = list(getattr(response, "tool_calls", None) or [])
    if not calls:
        return []
    call = calls[0]
    arguments = call.get("args", {}) if isinstance(call, dict) else getattr(call, "args", {})
    return sorted(arguments) if isinstance(arguments, dict) else []


async def run(output_path: Path) -> dict[str, Any]:
    settings = get_settings()
    if settings.dashscope_api_key is None or not settings.dashscope_api_key.get_secret_value():
        raise RuntimeError("DASHSCOPE_API_KEY 未配置，无法运行真实联调")

    # 通过分类器构造绑定工具的模型，然后在调用外包一层记录器。
    classifier = BailianIntentClassifier(
        model_name=settings.tool_routing_model_name,
        base_url=settings.answer_base_url,
        api_key=settings.dashscope_api_key,
        temperature=settings.tool_routing_temperature,
        timeout_seconds=settings.tool_routing_timeout_seconds,
        max_retries=settings.tool_routing_max_retries,
    )
    recording_model = _RecordingModel(classifier._get_model())
    classifier._model = recording_model

    cases: list[dict[str, Any]] = []
    for dataset_path in DATASET_PATHS:
        dataset = load_eval_dataset(dataset_path)
        for case in dataset.cases:
            recording_model.last_response = None
            started = time.perf_counter()
            actual: dict[str, Any] = {}
            error: dict[str, str] | None = None
            try:
                decision = await classifier.classify(case.question)
                actual = {
                    "route": decision.capability,
                    "arguments": decision.arguments.model_dump() if decision.arguments else None,
                }
            except ToolRoutingProviderError as exc:
                error = {"type": _error_kind(exc), "message": str(exc)}
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            expected = {
                "route": case.expected_route,
                "tool": case.expected_tool,
                "arguments": case.expected_arguments,
                "clarification_required": case.clarification_required,
            }
            actual_tool = {
                "route": actual.get("route"),
                "arguments": actual.get("arguments"),
            }
            model_tool_name = _model_tool_name(recording_model.last_response)
            model_argument_keys = _model_argument_keys(recording_model.last_response)
            passed = (
                error is None
                and actual_tool["route"] == expected["route"]
                and actual_tool["arguments"] == expected["arguments"]
            )
            cases.append(
                {
                    "case_id": case.id,
                    "dataset_version": dataset.dataset_version,
                    "question": case.question,
                    "expected": expected,
                    "actual": actual or None,
                    "model_tool_name": model_tool_name,
                    "model_argument_keys": model_argument_keys,
                    "error": error,
                    "passed": passed,
                    "latency_ms": elapsed_ms,
                    **_usage(recording_model.last_response),
                }
            )

    claim_cases = [
        case
        for case in cases
        if case["expected"]["route"] in {"reimbursement_status", "compliance_precheck"}
    ]
    summary = {
        "total": len(cases),
        "passed": sum(case["passed"] for case in cases),
        "failed": sum(not case["passed"] for case in cases),
        "provider_errors": sum(case["error"] is not None for case in cases),
        "route_accuracy": round(
            sum(
                case["error"] is None
                and case["actual"] is not None
                and case["actual"]["route"] == case["expected"]["route"]
                for case in cases
            )
            / len(cases),
            4,
        ),
        "tool_name_accuracy_non_clarification": round(
            sum(
                case["model_tool_name"] == case["expected"]["tool"]
                for case in cases
                if case["expected"]["tool"] and not case["expected"]["clarification_required"]
            )
            / sum(
                bool(case["expected"]["tool"]) and not case["expected"]["clarification_required"]
                for case in cases
            ),
            4,
        ),
        "spurious_tool_calls_on_policy": sum(
            case["expected"]["tool"] is None and case["model_tool_name"] is not None
            for case in cases
        ),
        "clarification_match": sum(
            case["actual"] is not None
            and case["actual"]["arguments"] is None
            and case["expected"]["clarification_required"]
            for case in cases
        ),
        "clarification_expected": sum(
            case["expected"]["clarification_required"] for case in cases
        ),
        "claim_number_clarification_match": sum(
            case["actual"] is not None
            and case["actual"]["arguments"] is None
            and case["expected"]["clarification_required"]
            for case in claim_cases
        ),
        "claim_number_clarification_expected": sum(
            case["expected"]["clarification_required"] for case in claim_cases
        ),
        "unauthorized_argument_errors": sum(
            case["error"] is not None
            and case["error"]["type"] == "unauthorized_or_invalid_arguments"
            for case in cases
        ),
        "average_latency_ms": round(sum(case["latency_ms"] for case in cases) / len(cases), 2),
        "input_tokens": sum(
            case["input_tokens"] or 0 for case in cases
        ),
        "output_tokens": sum(
            case["output_tokens"] or 0 for case in cases
        ),
    }
    result = {
        "run_id": str(uuid4()),
        "started_at": datetime.now(UTC).isoformat(),
        "provider": "bailian",
        "model_name": settings.tool_routing_model_name,
        "base_url": settings.answer_base_url,
        "temperature": settings.tool_routing_temperature,
        "datasets": [str(path.relative_to(PROJECT_ROOT)) for path in DATASET_PATHS],
        "summary": summary,
        "cases": cases,
        "notes": [
            "本次只验证 Function Calling 路由和参数，不执行数据库工具。",
            "费用由百炼账单为准；若响应未返回 token 元数据，结果中的 token 为 0。",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 Stage 13 百炼 Function Calling 联调")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(f"结果已写入: {args.output}")


if __name__ == "__main__":
    main()
