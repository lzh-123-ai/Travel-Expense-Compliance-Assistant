"""保留可复现证据日志的离线 Stage 11 Prompt 对照运行器。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete

from app.core.config import PROJECT_ROOT, get_settings
from app.db.session import async_session_factory, close_database_connections
from app.evaluation.answer_metrics import aggregate_answer_metrics, score_answer_case
from app.evaluation.contracts import EvalCase
from app.evaluation.loader import load_eval_dataset
from app.evaluation.stage9_baseline import (
    EXPECTED_MODEL_SHA256,
    MANIFEST_PATH,
    MODEL_SOURCE,
    MODEL_SOURCE_ID,
    MODEL_SOURCE_REVISION,
    _import_documents,
)
from app.evaluation.stage10_baseline import _index_keywords
from app.models.knowledge_base import KnowledgeBase
from app.services.answer_prompts import (
    PROMPT_REVISIONS,
    SUPPORTED_PROMPT_VERSIONS,
    PromptVersion,
)
from app.services.answering import (
    AnswerOutputError,
    AnswerProvider,
    AnswerResult,
    AnswerService,
)
from app.services.bailian_answer_provider import AnswerProviderError, get_answer_provider
from app.services.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.services.retrieval import HybridRetrievalService
from app.services.storage import LocalStorage

DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage8_v0.json"
TUNING_DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage11_tuning_v0.json"
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "results" / "stage11_prompt_comparison.json"
)
DEFAULT_PROMPT_VERSIONS: tuple[PromptVersion, ...] = ("v0", "v1")
ROLE_SCOPES = {
    "employee": frozenset({"all_employees"}),
    "finance_reviewer": frozenset({"all_employees", "finance_only"}),
    "policy_admin": frozenset({"all_employees", "finance_only"}),
}


def _evaluation_purpose(dataset_version: str) -> str:
    if dataset_version.startswith("stage11-tuning-"):
        return "prompt_tuning"
    return "frozen_regression"


def _serialize_answer(result: AnswerResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "answer": result.answer,
        "missing_information": list(result.missing_information),
        "citations": [
            {
                "source_id": citation.source_id,
                "chunk_id": str(citation.chunk_id),
                "document_id": str(citation.document_id),
                "original_filename": citation.original_filename,
                "version_label": citation.version_label,
                "content": citation.content,
                "page_start": citation.page_start,
                "page_end": citation.page_end,
                "section_path": list(citation.section_path),
            }
            for citation in result.citations
        ],
        "warnings": [asdict(warning) for warning in result.warnings],
        "version_conflicts": [asdict(conflict) for conflict in result.version_conflicts],
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "prompt_revision": result.prompt_revision,
        "retrieved_sources": [
            {
                "source_id": item.source_id,
                "chunk_id": str(item.hit.chunk_id),
                "document_id": str(item.hit.document_id),
                "original_filename": item.hit.original_filename,
                "version_label": item.hit.version_label,
                "content": item.hit.content,
                "page_start": item.hit.page_start,
                "page_end": item.hit.page_end,
                "section_path": list(item.hit.section_path),
                "dense_rank": item.hit.dense_rank,
                "keyword_rank": item.hit.keyword_rank,
                "rrf_score": item.hit.rrf_score,
                "policy_type": item.hit.policy_type,
                "effective_from": item.hit.effective_from.isoformat(),
                "effective_to": (
                    item.hit.effective_to.isoformat() if item.hit.effective_to else None
                ),
            }
            for item in result.evidence
        ],
    }


async def _evaluate_prompt(
    *,
    prompt_version: PromptVersion,
    knowledge_base_id: UUID,
    embedding_provider: SentenceTransformerEmbeddingProvider,
    answer_provider: AnswerProvider,
    dataset: Any,
    concurrency_limiter: asyncio.Semaphore,
) -> dict[str, Any]:
    async def evaluate_case(case: EvalCase) -> dict[str, Any]:
        async with concurrency_limiter:
            started = perf_counter()
            try:
                async with async_session_factory() as session:
                    result = await AnswerService(HybridRetrievalService()).answer(
                        session,
                        embedding_provider,
                        answer_provider,
                        knowledge_base_id=knowledge_base_id,
                        question=case.question,
                        expense_date=case.expense_date,
                        allowed_scopes=ROLE_SCOPES[case.role],
                        top_k=5,
                        prompt_version=prompt_version,
                    )
                latency_ms = (perf_counter() - started) * 1000
                cited_labels = [
                    citation.version_label
                    for citation in result.citations
                    if citation.version_label is not None
                ]
                metric = score_answer_case(
                    case,
                    actual_status=result.status,
                    cited_version_labels=cited_labels,
                )
                serialized = _serialize_answer(result)
                case_result = {
                    **asdict(metric),
                    "question": case.question,
                    "role": case.role,
                    "expense_date": case.expense_date.isoformat() if case.expense_date else None,
                    "acceptable_answer_points": case.acceptable_answer_points,
                    "latency_ms": round(latency_ms, 3),
                    **serialized,
                }
                usage = result.usage
                provider_error = False
                error_code = None
            except (AnswerProviderError, AnswerOutputError) as exc:
                latency_ms = (perf_counter() - started) * 1000
                metric = score_answer_case(
                    case,
                    actual_status="provider_error",
                    cited_version_labels=[],
                )
                case_result = {
                    **asdict(metric),
                    "question": case.question,
                    "role": case.role,
                    "expense_date": case.expense_date.isoformat() if case.expense_date else None,
                    "acceptable_answer_points": case.acceptable_answer_points,
                    "latency_ms": round(latency_ms, 3),
                    "error": type(exc).__name__,
                    "error_code": (
                        exc.code if isinstance(exc, AnswerProviderError) else "untrusted_citation"
                    ),
                }
                usage = exc.usage if isinstance(exc, AnswerProviderError) else None
                provider_error = True
                error_code = case_result["error_code"]
            print(
                f"[{prompt_version}] {case.id}: {metric.actual_status} "
                f"hard_pass={metric.hard_passed} latency_ms={latency_ms:.0f}",
                flush=True,
            )
            return {
                "metric": metric,
                "case": case_result,
                "latency_ms": latency_ms,
                "usage": usage,
                "provider_error": provider_error,
                "error_code": error_code,
            }

    outcomes = await asyncio.gather(*(evaluate_case(case) for case in dataset.cases))
    metrics = [outcome["metric"] for outcome in outcomes]
    case_results = [outcome["case"] for outcome in outcomes]
    latencies_ms = [outcome["latency_ms"] for outcome in outcomes]
    usages = [outcome["usage"] for outcome in outcomes if outcome["usage"] is not None]
    input_tokens = sum(usage.input_tokens or 0 for usage in usages)
    output_tokens = sum(usage.output_tokens or 0 for usage in usages)
    model_calls = sum(usage.input_tokens is not None for usage in usages)
    provider_errors = sum(outcome["provider_error"] for outcome in outcomes)
    model_output_errors = sum(
        outcome["error_code"]
        in {"invalid_json", "schema_validation", "unsupported_content", "untrusted_citation"}
        for outcome in outcomes
    )
    request_errors = sum(outcome["error_code"] == "request_failed" for outcome in outcomes)
    configuration_errors = sum(
        outcome["error_code"] == "configuration_error" for outcome in outcomes
    )

    aggregate = asdict(aggregate_answer_metrics(metrics))
    aggregate.update(
        {
            "model_calls": model_calls,
            "provider_errors": provider_errors,
            "model_output_errors": model_output_errors,
            "request_errors": request_errors,
            "configuration_errors": configuration_errors,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": {
                "mean": round(sum(latencies_ms) / len(latencies_ms), 3),
                "max": round(max(latencies_ms), 3),
            },
        }
    )
    return {"aggregate": aggregate, "cases": case_results}


async def run_comparison(
    model_path: Path,
    output_path: Path,
    *,
    concurrency: int = 4,
    dataset_path: Path = DATASET_PATH,
    prompt_versions: tuple[PromptVersion, ...] = DEFAULT_PROMPT_VERSIONS,
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("Concurrency must be at least one")
    settings = get_settings()
    model_path = model_path.resolve()
    weight_path = model_path / "model.safetensors"
    if not weight_path.is_file():
        raise FileNotFoundError(f"Missing local model weight: {weight_path}")
    with weight_path.open("rb") as weight_file:
        model_sha256 = hashlib.file_digest(weight_file, "sha256").hexdigest()
    if model_sha256 != EXPECTED_MODEL_SHA256:
        raise ValueError("Local model weight does not match the frozen retrieval baseline")

    dataset_path = dataset_path.resolve()
    dataset = load_eval_dataset(dataset_path)
    if not prompt_versions:
        raise ValueError("At least one prompt version is required")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    run_id = f"stage11-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    knowledge_base = KnowledgeBase(name=f"__stage11_baseline_{run_id}")
    storage = LocalStorage(settings.upload_dir)
    embedding_provider = SentenceTransformerEmbeddingProvider(
        model_name=settings.embedding_model_name,
        model_path=model_path,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
        query_instruction=settings.embedding_query_instruction,
    )
    answer_provider = get_answer_provider()
    storage_keys: list[str] = []

    async with async_session_factory() as session:
        session.add(knowledge_base)
        await session.commit()
        await session.refresh(knowledge_base)

    started_at = datetime.now(UTC)
    try:
        total_chunks, embedded_chunks = await _import_documents(
            knowledge_base_id=knowledge_base.id,
            run_id=run_id,
            manifest=manifest,
            storage=storage,
            provider=embedding_provider,
            storage_keys=storage_keys,
        )
        keyword_chunks, indexed_chunks = await _index_keywords(knowledge_base.id)
        if keyword_chunks != total_chunks:
            raise RuntimeError("Dense and keyword indexes do not cover the same chunks")

        concurrency_limiter = asyncio.Semaphore(concurrency)
        prompt_results = await asyncio.gather(
            *(
                _evaluate_prompt(
                    prompt_version=prompt_version,
                    knowledge_base_id=knowledge_base.id,
                    embedding_provider=embedding_provider,
                    answer_provider=answer_provider,
                    dataset=dataset,
                    concurrency_limiter=concurrency_limiter,
                )
                for prompt_version in prompt_versions
            )
        )
        prompts = dict(zip(prompt_versions, prompt_results, strict=True))
        evaluation_purpose = _evaluation_purpose(dataset.dataset_version)

        report = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "dataset_version": dataset.dataset_version,
            "dataset_annotation_status": dataset.annotation_status,
            "dataset_path": str(dataset_path),
            "evaluation_purpose": evaluation_purpose,
            "formal_baseline": evaluation_purpose == "frozen_regression"
            and dataset.annotation_status == "reviewed"
            and all(
                prompt["aggregate"]["request_errors"] == 0
                and prompt["aggregate"]["configuration_errors"] == 0
                for prompt in prompts.values()
            ),
            "metric_scope": (
                "status, expected/forbidden document-version citations; "
                "answer point correctness pending human review"
            ),
            "retrieval_config_version": "stage10-hybrid-rrf-v1",
            "embedding_provider": embedding_provider.provider_name,
            "embedding_model": embedding_provider.model_name,
            "embedding_model_source": MODEL_SOURCE,
            "embedding_model_source_id": MODEL_SOURCE_ID,
            "embedding_model_source_revision": MODEL_SOURCE_REVISION,
            "embedding_model_weight_sha256": model_sha256,
            "answer_provider": answer_provider.provider_name,
            "answer_model": answer_provider.model_name,
            "answer_base_url": settings.answer_base_url,
            "answer_temperature": settings.answer_temperature,
            "answer_enable_thinking": settings.answer_enable_thinking,
            "answer_concurrency": concurrency,
            "prompt_revisions": {
                version_name: PROMPT_REVISIONS[version_name]
                for version_name in prompt_versions
            },
            "top_k": 5,
            "documents": len(manifest["documents"]),
            "chunks": total_chunks,
            "embedded_chunks": embedded_chunks,
            "keyword_indexed_chunks": indexed_chunks,
            "environment": {
                "python": platform.python_version(),
                "langchain_core": version("langchain-core"),
                "langchain_openai": version("langchain-openai"),
                "openai": version("openai"),
                "sentence_transformers": version("sentence-transformers"),
            },
            "prompts": prompts,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        return report
    finally:
        try:
            async with async_session_factory() as session:
                await session.execute(
                    delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base.id)
                )
                await session.commit()
        finally:
            for storage_key in storage_keys:
                storage.delete(storage_key)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Stage 11 answer prompts")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--prompts",
        nargs="+",
        choices=SUPPORTED_PROMPT_VERSIONS,
        default=list(DEFAULT_PROMPT_VERSIONS),
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    try:
        report = await run_comparison(
            args.model_path,
            args.output,
            concurrency=args.concurrency,
            dataset_path=args.dataset,
            prompt_versions=tuple(args.prompts),
        )
        print(
            json.dumps(
                {
                    "formal_baseline": report["formal_baseline"],
                    "prompts": {
                        name: value["aggregate"] for name, value in report["prompts"].items()
                    },
                    "output": str(args.output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await close_database_connections()


if __name__ == "__main__":
    asyncio.run(_main())
