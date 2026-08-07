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


class DenseSearchResponse(BaseModel):
    query: str
    expense_date: date
    provider_name: str
    model_name: str
    top_k: int
    searched_at: datetime
    hits: list[DenseSearchHitResponse]
