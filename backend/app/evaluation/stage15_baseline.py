"""查询重写与候选重排的四组离线评测运行器。

只验证检索候选质量与安全边界，不调用回答模型，也不修改线上依赖。
"""

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
from statistics import fmean
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import delete

from app.core.config import PROJECT_ROOT, get_settings
from app.db.session import async_session_factory, close_database_connections
from app.evaluation.loader import load_retrieval_eval_dataset
from app.evaluation.retrieval_metrics import aggregate_retrieval_metrics, score_retrieval_case
from app.evaluation.stage9_baseline import (
    EXPECTED_MODEL_SHA256,
    MANIFEST_PATH,
    MODEL_SOURCE,
    MODEL_SOURCE_ID,
    MODEL_SOURCE_REVISION,
    _import_documents,
)
from app.evaluation.stage10_baseline import _index_keywords, _latency_summary
from app.evaluation.stage15_ablation import (
    ABLATION_CONFIGS,
    Stage15Variant,
    build_retrieval_variant,
    ndcg_at_k,
)
from app.models.knowledge_base import KnowledgeBase
from app.services.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.services.retrieval import DenseRetrievalService, KeywordRetrievalService
from app.services.storage import LocalStorage

DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage10_retrieval_v0.json"
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "results" / "stage15_retrieval_ablation.json"
)


def _serialize_hit(hit: object) -> dict[str, Any]:
    """只保存评测所需的排名和来源字段，避免把整段制度内容写入报告。"""
    data: dict[str, Any] = {
        "chunk_id": str(hit.chunk_id),
        "document_id": str(hit.document_id),
        "version_label": hit.version_label,
        "section_path": list(hit.section_path),
        "rrf_score": round(hit.rrf_score, 6),
        "rerank_score": (
            round(hit.rerank_score, 6) if hit.rerank_score is not None else None
        ),
    }
    if hit.dense_similarity is not None:
        data["dense_similarity"] = round(hit.dense_similarity, 6)
    if hit.keyword_score is not None:
        data["keyword_score"] = round(hit.keyword_score, 6)
    return data


def _field_contract(case: Any, result: Any) -> dict[str, Any]:
    """检查检索副本是否仍保留原问题和受保护业务字段。"""
    rewrite = result.query_rewrite
    if rewrite is None:
        return {"checked": False, "preserved": True}
    constraints = rewrite.constraints
    protected_terms = (
        *constraints.locations,
        *constraints.expense_types,
        *constraints.persons,
        *constraints.negations,
    )
    protected_terms_preserved = all(term in rewrite.search_query for term in protected_terms)
    employee_scope_preserved = constraints.employee_scope == tuple(sorted(case.allowed_scopes))
    return {
        "checked": True,
        "preserved": (
            rewrite.original_query == case.query
            and constraints.expense_date == case.expense_date
            and employee_scope_preserved
            and protected_terms_preserved
        ),
        "original_query_preserved": rewrite.original_query == case.query,
        "expense_date_preserved": constraints.expense_date == case.expense_date,
        "employee_scope_preserved": employee_scope_preserved,
        "employee_scope": list(constraints.employee_scope),
        "protected_terms_preserved": protected_terms_preserved,
        "locations": list(constraints.locations),
        "expense_types": list(constraints.expense_types),
        "persons": list(constraints.persons),
        "search_query": rewrite.search_query,
        "negations": list(constraints.negations),
    }


async def run_ablation(
    model_path: Path,
    output_path: Path,
    *,
    dataset_path: Path = DATASET_PATH,
    allow_draft: bool = False,
) -> dict[str, Any]:
    """导入临时知识库，依次运行四组变体，最后清理全部临时数据。"""
    settings = get_settings()
    model_path = model_path.resolve()
    weight_path = model_path / "model.safetensors"
    if not weight_path.is_file():
        raise FileNotFoundError(f"Missing local model weight: {weight_path}")

    dataset = load_retrieval_eval_dataset(dataset_path)
    if dataset.annotation_status != "reviewed" and not allow_draft:
        raise ValueError("Stage 15 retrieval dataset must be reviewed before formal evaluation")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    with weight_path.open("rb") as weight_file:
        model_sha256 = hashlib.file_digest(weight_file, "sha256").hexdigest()
    if model_sha256 != EXPECTED_MODEL_SHA256:
        raise ValueError("Local model weight does not match the frozen retrieval baseline")

    run_id = f"stage15-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    knowledge_base = KnowledgeBase(name=f"__stage15_ablation_{run_id}")
    storage = LocalStorage(settings.upload_dir)
    provider = SentenceTransformerEmbeddingProvider(
        model_name=settings.embedding_model_name,
        model_path=model_path,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
        query_instruction=settings.embedding_query_instruction,
    )
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
            provider=provider,
            storage_keys=storage_keys,
        )
        keyword_chunks, indexed_chunks = await _index_keywords(knowledge_base.id)
        if keyword_chunks != total_chunks:
            raise RuntimeError("Dense and keyword indexes do not cover the same chunks")

        dense = DenseRetrievalService()
        keyword = KeywordRetrievalService()
        variants = {
            config.name: build_retrieval_variant(
                config.name,
                dense=dense,
                keyword=keyword,
            )
            for config in ABLATION_CONFIGS
        }
        metrics: dict[Stage15Variant, list[Any]] = {config.name: [] for config in ABLATION_CONFIGS}
        cases: dict[Stage15Variant, list[dict[str, Any]]] = {
            config.name: [] for config in ABLATION_CONFIGS
        }
        latencies: dict[Stage15Variant, list[float]] = {
            config.name: [] for config in ABLATION_CONFIGS
        }
        ndcg_values: dict[Stage15Variant, list[float]] = {
            config.name: [] for config in ABLATION_CONFIGS
        }
        field_checks: dict[Stage15Variant, list[bool]] = {
            config.name: [] for config in ABLATION_CONFIGS
        }

        async with async_session_factory() as session:
            for case in dataset.cases:
                common = {
                    "knowledge_base_id": knowledge_base.id,
                    "query": case.query,
                    "expense_date": case.expense_date,
                    "allowed_scopes": frozenset(case.allowed_scopes),
                    "top_k": case.top_k,
                }
                for config in ABLATION_CONFIGS:
                    started = perf_counter()
                    result = await variants[config.name].search(session, provider, **common)
                    latency_ms = (perf_counter() - started) * 1000
                    hits = result.hits
                    labels = [hit.version_label for hit in hits if hit.version_label is not None]
                    metric = score_retrieval_case(case, labels)
                    ndcg = ndcg_at_k(
                        labels,
                        set(case.expected_version_labels),
                        k=case.top_k,
                    )
                    field_contract = _field_contract(case, result)
                    metrics[config.name].append(metric)
                    ndcg_values[config.name].append(ndcg)
                    if field_contract["checked"]:
                        field_checks[config.name].append(field_contract["preserved"])
                    latencies[config.name].append(latency_ms)
                    cases[config.name].append(
                        {
                            **asdict(metric),
                            "query": case.query,
                            "expense_date": case.expense_date.isoformat(),
                            "allowed_scopes": case.allowed_scopes,
                            "strategy": result.strategy,
                            "reranked": result.reranked,
                            "ndcg_at_k": round(ndcg, 6),
                            "field_contract": field_contract,
                            "latency_ms": round(latency_ms, 3),
                            "hits": [_serialize_hit(hit) for hit in hits],
                        }
                    )
                    print(
                        f"[{config.name}] {case.id}: pass={metric.passed} "
                        f"ndcg={ndcg:.3f} latency_ms={latency_ms:.0f}",
                        flush=True,
                    )

        strategies: dict[str, Any] = {}
        for config in ABLATION_CONFIGS:
            name = config.name
            strategies[name] = {
                "config": asdict(config),
                "aggregate": asdict(aggregate_retrieval_metrics(metrics[name])),
                "ndcg_at_5": round(fmean(ndcg_values[name]), 6),
                "field_preservation_rate": (
                    round(fmean(field_checks[name]), 6) if field_checks[name] else None
                ),
                "hard_filter_leakage_count": sum(
                    len(item["retrieved_forbidden_labels"]) for item in cases[name]
                ),
                "latency_ms": _latency_summary(latencies[name]),
                "estimated_query_rewrite_cost_usd": 0.0,
                "estimated_rerank_cost_usd": 0.0,
                "cases": cases[name],
            }

        report = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "dataset_version": dataset.dataset_version,
            "dataset_annotation_status": dataset.annotation_status,
            "formal_baseline": dataset.annotation_status == "reviewed",
            "metric_scope": "document_version_labels_in_top_k_chunks",
            "provider": provider.provider_name,
            "model_name": provider.model_name,
            "model_source": MODEL_SOURCE,
            "model_source_id": MODEL_SOURCE_ID,
            "model_source_revision": MODEL_SOURCE_REVISION,
            "model_weight_sha256": model_sha256,
            "embedding_dimension": provider.dimension,
            "query_instruction": provider.query_instruction,
            "documents": len(manifest["documents"]),
            "chunks": total_chunks,
            "embedded_chunks": embedded_chunks,
            "keyword_indexed_chunks": indexed_chunks,
            "estimated_embedding_api_cost_usd": 0.0,
            "answer_evaluation": {
                "status": "not_run",
                "reason": (
                    "Stage 15 local runner only evaluates retrieval; answer-model comparison "
                    "requires separate authorization."
                ),
            },
            "environment": {
                "python": platform.python_version(),
                "sentence_transformers": version("sentence-transformers"),
                "torch": version("torch"),
                "pgvector": version("pgvector"),
            },
            "strategies": strategies,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
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
    parser = argparse.ArgumentParser(description="Run Stage 15 retrieval ablation")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--allow-draft", action="store_true")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    try:
        report = await run_ablation(
            args.model_path,
            args.output,
            dataset_path=args.dataset,
            allow_draft=args.allow_draft,
        )
        print(
            json.dumps(
                {
                    "formal_baseline": report["formal_baseline"],
                    "strategies": {
                        name: {
                            "aggregate": value["aggregate"],
                            "ndcg_at_5": value["ndcg_at_5"],
                            "latency_ms": value["latency_ms"],
                        }
                        for name, value in report["strategies"].items()
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
