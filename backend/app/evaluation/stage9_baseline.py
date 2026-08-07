from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
from dataclasses import asdict
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete

from app.core.config import PROJECT_ROOT, get_settings
from app.db.session import async_session_factory, close_database_connections
from app.evaluation.loader import load_retrieval_eval_dataset
from app.evaluation.retrieval_metrics import (
    aggregate_retrieval_metrics,
    score_retrieval_case,
)
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services.document_processing import DocumentProcessingService
from app.services.document_validation import validate_stored_content, validate_upload_metadata
from app.services.embeddings.indexing import DocumentEmbeddingService
from app.services.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.services.retrieval import DenseRetrievalService
from app.services.storage import LocalStorage

DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "stage9_retrieval_v0.json"
MANIFEST_PATH = PROJECT_ROOT / "data" / "policies" / "manifest.json"
POLICY_ROOT = PROJECT_ROOT / "data" / "policies"
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "results" / "stage9_bge_dense_baseline.json"
)
MODEL_SOURCE = "ModelScope"
MODEL_SOURCE_ID = "AI-ModelScope/bge-small-zh-v1.5"
MODEL_SOURCE_REVISION = "master"
EXPECTED_MODEL_SHA256 = "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026"


class AsyncBytesReader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.content):
            return b""
        if size < 0:
            size = len(self.content) - self.offset
        start = self.offset
        self.offset = min(len(self.content), self.offset + size)
        return self.content[start : self.offset]


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


async def _import_documents(
    *,
    knowledge_base_id: UUID,
    run_id: str,
    manifest: dict[str, Any],
    storage: LocalStorage,
    provider: SentenceTransformerEmbeddingProvider,
    storage_keys: list[str],
) -> tuple[int, int]:
    document_ids: dict[str, UUID] = {}
    total_chunks = 0
    embedded_chunks = 0
    processor = DocumentProcessingService()
    embedder = DocumentEmbeddingService()
    settings = get_settings()

    async with async_session_factory() as session:
        for item in manifest["documents"]:
            source_path = POLICY_ROOT / item["relative_path"]
            content = source_path.read_bytes()
            metadata = validate_upload_metadata(source_path.name, "text/markdown")
            document_id = uuid4()
            storage_key = f"evaluations/{run_id}/{document_id}{source_path.suffix}"
            stored = await storage.save(
                AsyncBytesReader(content),
                storage_key,
                settings.max_document_size_bytes,
            )
            storage_keys.append(storage_key)
            validate_stored_content(
                storage,
                storage_key,
                metadata.document_format,
                settings.max_docx_uncompressed_size_bytes,
            )
            supersedes_key = item.get("supersedes_document_key")
            document = Document(
                id=document_id,
                knowledge_base_id=knowledge_base_id,
                original_filename=metadata.filename,
                content_type=metadata.content_type,
                file_size=stored.size,
                storage_key=stored.key,
                sha256=stored.sha256,
                policy_type=item["policy_type"],
                version_label=item["version_label"],
                effective_from=_parse_date(item.get("effective_from")),
                effective_to=_parse_date(item.get("effective_to")),
                access_scope=item["access_scope"],
                supersedes_document_id=(
                    document_ids.get(supersedes_key) if supersedes_key else None
                ),
            )
            session.add(document)
            await session.commit()
            await processor.process(document, session, storage)
            if document.status != "ready":
                raise RuntimeError(
                    f"Baseline document {item['document_key']} failed processing: "
                    f"{document.error_message}"
                )
            result = await embedder.index_document(document, session, provider)
            total_chunks += result.total_chunks
            embedded_chunks += result.embedded_chunks
            document_ids[item["document_key"]] = document.id

    return total_chunks, embedded_chunks


async def run_baseline(model_path: Path, output_path: Path) -> dict[str, Any]:
    settings = get_settings()
    model_path = model_path.resolve()
    weight_path = model_path / "model.safetensors"
    if not weight_path.is_file():
        raise FileNotFoundError(f"Missing local model weight: {weight_path}")

    dataset = load_retrieval_eval_dataset(DATASET_PATH)
    if dataset.annotation_status != "reviewed":
        raise ValueError("Stage 9 retrieval dataset must be reviewed before baseline execution")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    with weight_path.open("rb") as weight_file:
        model_sha256 = hashlib.file_digest(weight_file, "sha256").hexdigest()
    if model_sha256 != EXPECTED_MODEL_SHA256:
        raise ValueError("Local model weight does not match the frozen Stage 9 baseline")
    run_id = f"stage9-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    knowledge_base = KnowledgeBase(name=f"__stage9_baseline_{run_id}")
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
        retriever = DenseRetrievalService()
        case_metrics = []
        case_results = []
        latencies_ms: list[float] = []
        async with async_session_factory() as session:
            for case in dataset.cases:
                started = perf_counter()
                hits = await retriever.search(
                    session,
                    provider,
                    knowledge_base_id=knowledge_base.id,
                    query=case.query,
                    expense_date=case.expense_date,
                    allowed_scopes=frozenset(case.allowed_scopes),
                    top_k=case.top_k,
                )
                latency_ms = (perf_counter() - started) * 1000
                latencies_ms.append(latency_ms)
                labels = [hit.version_label for hit in hits if hit.version_label is not None]
                metric = score_retrieval_case(case, labels)
                case_metrics.append(metric)
                case_results.append(
                    {
                        **asdict(metric),
                        "query": case.query,
                        "expense_date": case.expense_date.isoformat(),
                        "allowed_scopes": case.allowed_scopes,
                        "latency_ms": round(latency_ms, 3),
                        "hits": [
                            {
                                "chunk_id": str(hit.chunk_id),
                                "document_id": str(hit.document_id),
                                "version_label": hit.version_label,
                                "section_path": list(hit.section_path),
                                "similarity": round(hit.similarity, 6),
                            }
                            for hit in hits
                        ],
                    }
                )

        aggregate = aggregate_retrieval_metrics(case_metrics)
        report = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "dataset_version": dataset.dataset_version,
            "dataset_annotation_status": dataset.annotation_status,
            "retrieval_strategy": "pgvector_cosine_dense",
            "metric_scope": "document_version_labels_in_top_5_chunks",
            "provider": provider.provider_name,
            "model_name": provider.model_name,
            "model_source": MODEL_SOURCE,
            "model_source_id": MODEL_SOURCE_ID,
            "model_source_revision": MODEL_SOURCE_REVISION,
            "model_weight_sha256": model_sha256,
            "embedding_dimension": provider.dimension,
            "query_instruction": provider.query_instruction,
            "batch_size": provider.batch_size,
            "top_k": 5,
            "documents": len(manifest["documents"]),
            "chunks": total_chunks,
            "embedded_chunks": embedded_chunks,
            "aggregate": asdict(aggregate),
            "latency_ms": {
                "p50": round(_percentile(latencies_ms, 0.50), 3),
                "p95": round(_percentile(latencies_ms, 0.95), 3),
                "max": round(max(latencies_ms), 3),
            },
            "estimated_embedding_api_cost_usd": 0.0,
            "environment": {
                "python": platform.python_version(),
                "sentence_transformers": version("sentence-transformers"),
                "torch": version("torch"),
                "pgvector": version("pgvector"),
            },
            "cases": case_results,
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
    parser = argparse.ArgumentParser(description="Run the reviewed Stage 9 dense baseline")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    try:
        report = await run_baseline(args.model_path, args.output)
        print(
            json.dumps(
                {"aggregate": report["aggregate"], "output": str(args.output)},
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await close_database_connections()


if __name__ == "__main__":
    asyncio.run(_main())
