"""索引操作和检索诊断的 HTTP 数据结构。"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentEmbeddingResponse(BaseModel):
    document_id: UUID
    provider_name: str
    model_name: str
    dimension: int
    total_chunks: int
    embedded_chunks: int
    skipped_chunks: int


class DocumentKeywordIndexResponse(BaseModel):
    document_id: UUID
    tokenizer: str
    total_chunks: int
    indexed_chunks: int
    skipped_chunks: int


class DenseSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, Field(min_length=2, max_length=500)]
    expense_date: date
    top_k: Annotated[int, Field(ge=1, le=20)] = 5


class DenseSearchHitResponse(BaseModel):
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: list[str]
    similarity: float
    policy_type: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class DenseSearchResponse(BaseModel):
    query: str
    expense_date: date
    provider_name: str
    model_name: str
    top_k: int
    searched_at: datetime
    hits: list[DenseSearchHitResponse]


class KeywordSearchHitResponse(BaseModel):
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: list[str]
    keyword_score: float
    policy_type: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class KeywordSearchResponse(BaseModel):
    query: str
    expense_date: date
    tokenizer: str
    top_k: int
    searched_at: datetime
    hits: list[KeywordSearchHitResponse]


class VersionConflictResponse(BaseModel):
    policy_type: str
    version_labels: list[str]


class HybridSearchHitResponse(BaseModel):
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: list[str]
    dense_similarity: float | None
    keyword_score: float | None
    dense_rank: int | None
    keyword_rank: int | None
    rrf_score: float
    document_rrf_score: float
    policy_type: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class HybridSearchResponse(BaseModel):
    query: str
    expense_date: date
    provider_name: str
    model_name: str
    tokenizer: str
    top_k: int
    searched_at: datetime
    version_conflicts: list[VersionConflictResponse]
    hits: list[HybridSearchHitResponse]
