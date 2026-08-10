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

from sqlalchemy import delete, select

from app.core.config import PROJECT_ROOT, get_settings
from app.db.session import async_session_factory, close_database_connections
from app.evaluation.loader import load_retrieval_eval_dataset
from app.evaluation.retrieval_metrics import (
    aggregate_retrieval_metrics,
    score_retrieval_case,
)
from app.evaluation.stage9_baseline import (
    EXPECTED_MODEL_SHA256,
    MANIFEST_PATH,
    MODEL_SOURCE,
    MODEL_SOURCE_ID,
    MODEL_SOURCE_REVISION,
    _import_documents,
    _percentile,
)
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.services.keyword_indexing import DocumentKeywordIndexingService
from app.services.retrieval import (
    DEFAULT_RRF_K,
    DenseRetrievalService,
    HybridRetrievalService,
    KeywordRetrievalService,
)
from app.services.storage import LocalStorage

DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage10_retrieval_v0.json"
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "results" / "stage10_retrieval_comparison.json"
)


async def _index_keywords(knowledge_base_id: UUID) -> tuple[int, int]:
    total_chunks = 0
    indexed_chunks = 0
    indexer = DocumentKeywordIndexingService()
    async with async_session_factory() as session:
        result = await session.execute(
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.id)
        )
        for document in result.scalars().all():
            indexed = await indexer.index_document(document, session)
            total_chunks += indexed.total_chunks
            indexed_chunks += indexed.indexed_chunks
    return total_chunks, indexed_chunks


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "max": round(max(values), 3),
    }


def _serialize_hit(hit: object) -> dict[str, Any]:
    data = {
        "chunk_id": str(hit.chunk_id),
        "document_id": str(hit.document_id),
        "version_label": hit.version_label,
        "section_path": list(hit.section_path),
    }
    if hasattr(hit, "similarity"):
        data["dense_similarity"] = round(hit.similarity, 6)
    if getattr(hit, "keyword_score", None) is not None:
        data["keyword_score"] = round(hit.keyword_score, 6)
    if getattr(hit, "rrf_score", None) is not None:
        data["rrf_score"] = round(hit.rrf_score, 6)
        data["document_rrf_score"] = round(hit.document_rrf_score, 6)
        data["dense_rank"] = hit.dense_rank
        data["keyword_rank"] = hit.keyword_rank
    return data


async def run_comparison(
    model_path: Path,
    output_path: Path,
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    model_path = model_path.resolve()
    weight_path = model_path / "model.safetensors"
    if not weight_path.is_file():
        raise FileNotFoundError(f"Missing local model weight: {weight_path}")

    dataset = load_retrieval_eval_dataset(DATASET_PATH)
    if dataset.annotation_status != "reviewed" and not allow_draft:
        raise ValueError("Stage 10 retrieval dataset must be reviewed before a formal baseline")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    with weight_path.open("rb") as weight_file:
        model_sha256 = hashlib.file_digest(weight_file, "sha256").hexdigest()
    if model_sha256 != EXPECTED_MODEL_SHA256:
        raise ValueError("Local model weight does not match the frozen retrieval baseline")

    run_id = f"stage10-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    knowledge_base = KnowledgeBase(name=f"__stage10_baseline_{run_id}")
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
        hybrid = HybridRetrievalService(dense=dense, keyword=keyword)
        metrics_by_strategy: dict[str, list[Any]] = {
            "dense": [],
            "keyword": [],
            "hybrid": [],
        }
        cases_by_strategy: dict[str, list[dict[str, Any]]] = {
            "dense": [],
            "keyword": [],
            "hybrid": [],
        }
        latencies_by_strategy: dict[str, list[float]] = {
            "dense": [],
            "keyword": [],
            "hybrid": [],
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
                started = perf_counter()
                dense_hits = await dense.search(session, provider, **common)
                dense_latency = (perf_counter() - started) * 1000

                started = perf_counter()
                keyword_hits = await keyword.search(session, **common)
                keyword_latency = (perf_counter() - started) * 1000

                started = perf_counter()
                hybrid_result = await hybrid.search(session, provider, **common)
                hybrid_latency = (perf_counter() - started) * 1000

                strategy_hits = {
                    "dense": dense_hits,
                    "keyword": keyword_hits,
                    "hybrid": hybrid_result.hits,
                }
                strategy_latencies = {
                    "dense": dense_latency,
                    "keyword": keyword_latency,
                    "hybrid": hybrid_latency,
                }
                for strategy, hits in strategy_hits.items():
                    labels = [hit.version_label for hit in hits if hit.version_label is not None]
                    metric = score_retrieval_case(case, labels)
                    metrics_by_strategy[strategy].append(metric)
                    latency_ms = strategy_latencies[strategy]
                    latencies_by_strategy[strategy].append(latency_ms)
                    case_result = {
                        **asdict(metric),
                        "query": case.query,
                        "expense_date": case.expense_date.isoformat(),
                        "allowed_scopes": case.allowed_scopes,
                        "latency_ms": round(latency_ms, 3),
                        "hits": [_serialize_hit(hit) for hit in hits],
                    }
                    if strategy == "hybrid":
                        case_result["version_conflicts"] = [
                            asdict(conflict) for conflict in hybrid_result.version_conflicts
                        ]
                    cases_by_strategy[strategy].append(case_result)

        strategies = {}
        for strategy in ("dense", "keyword", "hybrid"):
            strategies[strategy] = {
                "aggregate": asdict(aggregate_retrieval_metrics(metrics_by_strategy[strategy])),
                "latency_ms": _latency_summary(latencies_by_strategy[strategy]),
                "cases": cases_by_strategy[strategy],
            }
        report = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "dataset_version": dataset.dataset_version,
            "dataset_annotation_status": dataset.annotation_status,
            "formal_baseline": dataset.annotation_status == "reviewed",
            "metric_scope": "document_version_labels_in_top_5_chunks",
            "provider": provider.provider_name,
            "model_name": provider.model_name,
            "model_source": MODEL_SOURCE,
            "model_source_id": MODEL_SOURCE_ID,
            "model_source_revision": MODEL_SOURCE_REVISION,
            "model_weight_sha256": model_sha256,
            "embedding_dimension": provider.dimension,
            "query_instruction": provider.query_instruction,
            "keyword_tokenizer": keyword.tokenizer,
            "rrf_k": DEFAULT_RRF_K,
            "top_k": 5,
            "documents": len(manifest["documents"]),
            "chunks": total_chunks,
            "embedded_chunks": embedded_chunks,
            "keyword_indexed_chunks": indexed_chunks,
            "estimated_embedding_api_cost_usd": 0.0,
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
    parser = argparse.ArgumentParser(description="Compare Stage 10 retrieval strategies")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--allow-draft", action="store_true")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    try:
        report = await run_comparison(
            args.model_path,
            args.output,
            allow_draft=args.allow_draft,
        )
        print(
            json.dumps(
                {
                    "formal_baseline": report["formal_baseline"],
                    "strategies": {
                        name: value["aggregate"] for name, value in report["strategies"].items()
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
