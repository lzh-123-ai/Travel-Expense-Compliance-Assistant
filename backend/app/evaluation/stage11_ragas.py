from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any

from app.core.config import PROJECT_ROOT, get_settings

DEFAULT_INPUT_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "results" / "stage11_prompt_comparison.json"
)
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "results" / "stage11_ragas.json"
)
METRIC_NAMES = ("faithfulness", "factual_correctness", "context_recall")


def build_ragas_samples(
    report: dict[str, Any],
    *,
    prompt_names: tuple[str, ...],
    case_ids: frozenset[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert frozen answer evidence into judge inputs without rerunning retrieval."""
    samples: dict[str, list[dict[str, Any]]] = {}
    available_prompts = report.get("prompts", {})
    for prompt_name in prompt_names:
        if prompt_name not in available_prompts:
            raise ValueError(f"Prompt {prompt_name!r} is missing from the answer report")
        prompt_samples = []
        for case in available_prompts[prompt_name]["cases"]:
            if case_ids is not None and case["case_id"] not in case_ids:
                continue
            if case.get("expected_status") != "answered" or case.get("status") != "answered":
                continue
            contexts = [source["content"] for source in case.get("retrieved_sources", [])]
            answer_points = case.get("acceptable_answer_points", [])
            if not contexts or not answer_points:
                continue
            prompt_samples.append(
                {
                    "case_id": case["case_id"],
                    "user_input": case["question"],
                    "response": case["answer"],
                    "retrieved_contexts": contexts,
                    "reference": "；".join(answer_points),
                }
            )
        samples[prompt_name] = prompt_samples
    return samples


async def run_ragas(
    input_path: Path,
    output_path: Path,
    *,
    prompt_names: tuple[str, ...],
    case_ids: frozenset[str] | None = None,
    concurrency: int = 4,
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    started_at = datetime.now(UTC)
    started_clock = perf_counter()
    try:
        from openai import AsyncOpenAI
        from ragas.llms import llm_factory
        from ragas.metrics.collections import ContextRecall, FactualCorrectness, Faithfulness
    except ImportError as exc:
        raise RuntimeError(
            "RAGAS evaluation dependencies are missing; install the project evaluation extra"
        ) from exc

    settings = get_settings()
    if settings.dashscope_api_key is None:
        raise RuntimeError("DASHSCOPE_API_KEY is required for RAGAS evaluation")
    input_path = input_path.resolve()
    raw_report = input_path.read_bytes()
    report = json.loads(raw_report.decode("utf-8"))
    samples = build_ragas_samples(report, prompt_names=prompt_names, case_ids=case_ids)
    client = AsyncOpenAI(
        api_key=settings.dashscope_api_key.get_secret_value(),
        base_url=settings.answer_base_url,
        timeout=settings.answer_timeout_seconds,
        max_retries=settings.answer_max_retries,
    )
    judge = llm_factory(
        settings.answer_model_name,
        provider="openai",
        client=client,
        temperature=0,
    )
    scorers = {
        "faithfulness": Faithfulness(llm=judge),
        "factual_correctness": FactualCorrectness(llm=judge),
        "context_recall": ContextRecall(llm=judge),
    }
    concurrency_limiter = asyncio.Semaphore(concurrency)

    async def evaluate_metric(
        metric_name: str,
        scorer: Any,
        arguments: dict[str, Any],
    ) -> tuple[str, float | None, str | None, str | None]:
        try:
            async with concurrency_limiter:
                metric_result = await scorer.ascore(**arguments)
            return (
                metric_name,
                float(metric_result.value),
                getattr(metric_result, "reason", None),
                None,
            )
        except Exception as exc:  # The report must retain individual judge failures.
            return metric_name, None, None, f"{type(exc).__name__}: {exc}"

    async def evaluate_case(sample: dict[str, Any]) -> dict[str, Any]:
        metric_arguments = {
            "faithfulness": {
                "user_input": sample["user_input"],
                "response": sample["response"],
                "retrieved_contexts": sample["retrieved_contexts"],
            },
            "factual_correctness": {
                "response": sample["response"],
                "reference": sample["reference"],
            },
            "context_recall": {
                "user_input": sample["user_input"],
                "retrieved_contexts": sample["retrieved_contexts"],
                "reference": sample["reference"],
            },
        }
        metric_results = await asyncio.gather(
            *(
                evaluate_metric(metric_name, scorer, metric_arguments[metric_name])
                for metric_name, scorer in scorers.items()
            )
        )
        scores = {name: score for name, score, _, _ in metric_results}
        reasons = {name: reason for name, _, reason, _ in metric_results}
        errors = {
            name: error for name, _, _, error in metric_results if error is not None
        }
        print(f"[ragas] {sample['case_id']} {scores}", flush=True)
        return {
            "case_id": sample["case_id"],
            "scores": scores,
            "reasons": reasons,
            "errors": errors,
        }

    prompt_results: dict[str, Any] = {}
    for prompt_name, prompt_samples in samples.items():
        case_results = list(await asyncio.gather(*(evaluate_case(s) for s in prompt_samples)))

        aggregate = {
            metric_name: _mean_available(
                case["scores"][metric_name] for case in case_results
            )
            for metric_name in METRIC_NAMES
        }
        prompt_results[prompt_name] = {
            "evaluated_cases": len(case_results),
            "aggregate": aggregate,
            "metric_errors": sum(len(case["errors"]) for case in case_results),
            "cases": case_results,
        }

    finished_at = datetime.now(UTC)
    result = {
        "run_id": f"stage11-ragas-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_seconds": round(perf_counter() - started_clock, 3),
        "source_report": str(input_path),
        "source_report_sha256": hashlib.sha256(raw_report).hexdigest(),
        "source_run_id": report.get("run_id"),
        "dataset_version": report.get("dataset_version"),
        "prompt_revisions": report.get("prompt_revisions", {}),
        "judge_provider": "bailian-openai-compatible",
        "judge_model": settings.answer_model_name,
        "judge_base_url": settings.answer_base_url,
        "judge_concurrency": concurrency,
        "ragas_version": _package_version("ragas"),
        "metrics": list(METRIC_NAMES),
        "scope": "answered cases only; auxiliary LLM-judged metrics",
        "usage_tracking": "judge token and monetary cost are not exposed by this runner",
        "prompts": prompt_results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _mean_available(values: Any) -> float | None:
    available = [value for value in values if value is not None]
    return fmean(available) if available else None


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run auxiliary RAGAS judge metrics")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--prompts", nargs="+", default=["v0", "v1"])
    parser.add_argument("--case-id", action="append", default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    result = await run_ragas(
        args.input,
        args.output,
        prompt_names=tuple(args.prompts),
        case_ids=frozenset(args.case_id) if args.case_id else None,
        concurrency=args.concurrency,
    )
    print(
        json.dumps(
            {"output": str(args.output), "prompts": result["prompts"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
