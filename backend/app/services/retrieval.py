from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.embeddings import EmbeddingProvider, validate_embedding_vectors
from app.services.keywording import KEYWORD_TOKENIZER_VERSION, tokenize

PUBLIC_DOCUMENT_SCOPES = frozenset({"all_employees"})
DEFAULT_RRF_K = 60
DEFAULT_HYBRID_DIVERSITY_SLOTS = 3


def _document_scope_filters(
    *,
    knowledge_base_id: UUID,
    expense_date: date,
    allowed_scopes: frozenset[str],
) -> tuple[object, ...]:
    return (
        Document.knowledge_base_id == knowledge_base_id,
        Document.status == "ready",
        Document.access_scope.in_(allowed_scopes),
        or_(Document.effective_from.is_(None), Document.effective_from <= expense_date),
        or_(Document.effective_to.is_(None), Document.effective_to >= expense_date),
    )


@dataclass(frozen=True)
class DenseSearchHit:
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    cosine_distance: float
    policy_type: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None

    @property
    def similarity(self) -> float:
        return 1.0 - self.cosine_distance


@dataclass(frozen=True)
class KeywordSearchHit:
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    keyword_score: float
    policy_type: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class DenseRetrievalService:
    async def search(
        self,
        session: AsyncSession,
        provider: EmbeddingProvider,
        *,
        knowledge_base_id: UUID,
        query: str,
        expense_date: date,
        allowed_scopes: frozenset[str],
        top_k: int,
    ) -> tuple[DenseSearchHit, ...]:
        if not allowed_scopes:
            return ()
        query_vector = await provider.embed_query(query)
        vector = validate_embedding_vectors(
            (query_vector,), expected_count=1, expected_dimension=provider.dimension
        )[0]
        distance = DocumentChunk.embedding.cosine_distance(list(vector))
        statement = (
            select(DocumentChunk, Document, distance.label("cosine_distance"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                *_document_scope_filters(
                    knowledge_base_id=knowledge_base_id,
                    expense_date=expense_date,
                    allowed_scopes=allowed_scopes,
                ),
                DocumentChunk.embedding.is_not(None),
                DocumentChunk.embedding_provider == provider.provider_name,
                DocumentChunk.embedding_model == provider.model_name,
                DocumentChunk.embedding_dimension == provider.dimension,
                DocumentChunk.embedding_content_hash == DocumentChunk.content_hash,
            )
            .order_by(distance, Document.version_label, DocumentChunk.ordinal)
            .limit(top_k)
        )
        result = await session.execute(statement)
        return tuple(
            DenseSearchHit(
                chunk_id=chunk.id,
                document_id=document.id,
                original_filename=document.original_filename,
                version_label=document.version_label,
                content=chunk.content,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=tuple(chunk.section_path),
                cosine_distance=float(cosine_distance),
                policy_type=document.policy_type,
                effective_from=document.effective_from,
                effective_to=document.effective_to,
            )
            for chunk, document, cosine_distance in result.all()
        )


class KeywordRetrievalService:
    def __init__(self, tokenizer: str = KEYWORD_TOKENIZER_VERSION) -> None:
        self.tokenizer = tokenizer

    async def search(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: UUID,
        query: str,
        expense_date: date,
        allowed_scopes: frozenset[str],
        top_k: int,
    ) -> tuple[KeywordSearchHit, ...]:
        if not allowed_scopes:
            return ()
        query_terms = tokenize(query)
        if not query_terms:
            return ()
        vector = func.to_tsvector("simple", DocumentChunk.keyword_search_text)
        ts_query = func.websearch_to_tsquery("simple", " OR ".join(query_terms))
        rank = func.ts_rank_cd(vector, ts_query)
        statement = (
            select(DocumentChunk, Document, rank.label("keyword_score"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                *_document_scope_filters(
                    knowledge_base_id=knowledge_base_id,
                    expense_date=expense_date,
                    allowed_scopes=allowed_scopes,
                ),
                DocumentChunk.keyword_search_text.is_not(None),
                DocumentChunk.keyword_tokenizer == self.tokenizer,
                DocumentChunk.keyword_content_hash == DocumentChunk.content_hash,
                vector.op("@@")(ts_query),
            )
            .order_by(rank.desc(), Document.version_label, DocumentChunk.ordinal)
            .limit(top_k)
        )
        result = await session.execute(statement)
        return tuple(
            KeywordSearchHit(
                chunk_id=chunk.id,
                document_id=document.id,
                original_filename=document.original_filename,
                version_label=document.version_label,
                content=chunk.content,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=tuple(chunk.section_path),
                keyword_score=float(keyword_score),
                policy_type=document.policy_type,
                effective_from=document.effective_from,
                effective_to=document.effective_to,
            )
            for chunk, document, keyword_score in result.all()
        )


@dataclass(frozen=True)
class VersionConflict:
    policy_type: str
    version_labels: tuple[str, ...]


@dataclass(frozen=True)
class HybridSearchHit:
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    dense_similarity: float | None
    keyword_score: float | None
    dense_rank: int | None
    keyword_rank: int | None
    rrf_score: float
    document_rrf_score: float = 0.0
    policy_type: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


@dataclass(frozen=True)
class HybridSearchResult:
    hits: tuple[HybridSearchHit, ...]
    version_conflicts: tuple[VersionConflict, ...]


class HybridRetrievalService:
    def __init__(
        self,
        dense: DenseRetrievalService | None = None,
        keyword: KeywordRetrievalService | None = None,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        diversity_slots: int = DEFAULT_HYBRID_DIVERSITY_SLOTS,
    ) -> None:
        self.dense = dense or DenseRetrievalService()
        self.keyword = keyword or KeywordRetrievalService()
        self.rrf_k = rrf_k
        self.diversity_slots = diversity_slots

    async def search(
        self,
        session: AsyncSession,
        provider: EmbeddingProvider,
        *,
        knowledge_base_id: UUID,
        query: str,
        expense_date: date,
        allowed_scopes: frozenset[str],
        top_k: int,
        candidate_k: int | None = None,
    ) -> HybridSearchResult:
        if not allowed_scopes:
            return HybridSearchResult(hits=(), version_conflicts=())
        candidate_limit = candidate_k or max(top_k * 4, 20)
        dense_hits = await self.dense.search(
            session,
            provider,
            knowledge_base_id=knowledge_base_id,
            query=query,
            expense_date=expense_date,
            allowed_scopes=allowed_scopes,
            top_k=candidate_limit,
        )
        keyword_hits = await self.keyword.search(
            session,
            knowledge_base_id=knowledge_base_id,
            query=query,
            expense_date=expense_date,
            allowed_scopes=allowed_scopes,
            top_k=candidate_limit,
        )

        dense_by_id = {hit.chunk_id: hit for hit in dense_hits}
        keyword_by_id = {hit.chunk_id: hit for hit in keyword_hits}
        dense_ranks = {hit.chunk_id: rank for rank, hit in enumerate(dense_hits, start=1)}
        keyword_ranks = {hit.chunk_id: rank for rank, hit in enumerate(keyword_hits, start=1)}
        dense_document_ranks: dict[UUID, int] = {}
        keyword_document_ranks: dict[UUID, int] = {}
        for rank, hit in enumerate(dense_hits, start=1):
            dense_document_ranks.setdefault(hit.document_id, rank)
        for rank, hit in enumerate(keyword_hits, start=1):
            keyword_document_ranks.setdefault(hit.document_id, rank)
        document_rrf_scores = {
            document_id: sum(
                1.0 / (self.rrf_k + rank)
                for rank in (
                    dense_document_ranks.get(document_id),
                    keyword_document_ranks.get(document_id),
                )
                if rank is not None
            )
            for document_id in set(dense_document_ranks) | set(keyword_document_ranks)
        }
        all_ids = set(dense_by_id) | set(keyword_by_id)
        merged: list[HybridSearchHit] = []
        for chunk_id in all_ids:
            dense_hit = dense_by_id.get(chunk_id)
            keyword_hit = keyword_by_id.get(chunk_id)
            source = dense_hit or keyword_hit
            assert source is not None
            dense_rank = dense_ranks.get(chunk_id)
            keyword_rank = keyword_ranks.get(chunk_id)
            rrf_score = sum(
                1.0 / (self.rrf_k + rank) for rank in (dense_rank, keyword_rank) if rank is not None
            )
            merged.append(
                HybridSearchHit(
                    chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    original_filename=source.original_filename,
                    version_label=source.version_label,
                    content=source.content,
                    page_start=source.page_start,
                    page_end=source.page_end,
                    section_path=source.section_path,
                    dense_similarity=dense_hit.similarity if dense_hit else None,
                    keyword_score=keyword_hit.keyword_score if keyword_hit else None,
                    dense_rank=dense_rank,
                    keyword_rank=keyword_rank,
                    rrf_score=rrf_score,
                    document_rrf_score=document_rrf_scores[source.document_id],
                    policy_type=source.policy_type,
                    effective_from=source.effective_from,
                    effective_to=source.effective_to,
                )
            )
        merged.sort(
            key=lambda hit: (
                -hit.rrf_score,
                hit.version_label or "",
                hit.section_path,
                hit.content,
            )
        )
        hits = _select_document_aware_hits(
            merged,
            top_k=top_k,
            diversity_slots=self.diversity_slots,
        )
        return HybridSearchResult(hits=hits, version_conflicts=_detect_version_conflicts(hits))


def _select_document_aware_hits(
    merged: list[HybridSearchHit],
    *,
    top_k: int,
    diversity_slots: int,
) -> tuple[HybridSearchHit, ...]:
    by_document: dict[UUID, list[HybridSearchHit]] = {}
    for hit in merged:
        by_document.setdefault(hit.document_id, []).append(hit)
    document_order = sorted(
        by_document,
        key=lambda document_id: (
            -by_document[document_id][0].document_rrf_score,
            by_document[document_id][0].version_label or "",
            by_document[document_id][0].original_filename,
        ),
    )

    selected: list[HybridSearchHit] = []
    selected_ids: set[UUID] = set()
    for document_id in document_order[: min(top_k, diversity_slots)]:
        representative = by_document[document_id][0]
        selected.append(representative)
        selected_ids.add(representative.chunk_id)
    for hit in merged:
        if len(selected) >= top_k:
            break
        if hit.chunk_id not in selected_ids:
            selected.append(hit)
            selected_ids.add(hit.chunk_id)
    return tuple(selected)


def _detect_version_conflicts(hits: tuple[HybridSearchHit, ...]) -> tuple[VersionConflict, ...]:
    versions_by_policy: dict[str, set[str]] = {}
    for hit in hits:
        if hit.policy_type and hit.version_label:
            versions_by_policy.setdefault(hit.policy_type, set()).add(hit.version_label)
    return tuple(
        VersionConflict(policy_type=policy_type, version_labels=tuple(sorted(version_labels)))
        for policy_type, version_labels in sorted(versions_by_policy.items())
        if len(version_labels) > 1
    )


def get_dense_retrieval_service() -> DenseRetrievalService:
    return DenseRetrievalService()


def get_keyword_retrieval_service() -> KeywordRetrievalService:
    return KeywordRetrievalService(tokenizer=get_settings().keyword_tokenizer_version)


def get_hybrid_retrieval_service() -> HybridRetrievalService:
    return HybridRetrievalService(
        keyword=get_keyword_retrieval_service(),
    )
