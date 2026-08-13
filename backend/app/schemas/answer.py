"""基于证据的回答、引用和显式限制的 HTTP 数据结构。"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AnswerRequest(BaseModel):
    question: str = Field(min_length=4, max_length=2000)
    expense_date: date | None = None
    top_k: int = Field(default=5, ge=1, le=10)


class AnswerCitationResponse(BaseModel):
    source_id: str
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: list[str]


class AnswerWarningResponse(BaseModel):
    code: Literal["partial_text_extraction", "version_conflict"]
    message: str
    document_id: UUID | None


class AnswerVersionConflictResponse(BaseModel):
    policy_type: str
    version_labels: list[str]


class AnswerResponse(BaseModel):
    status: Literal["answered", "refused", "needs_clarification"]
    answer: str
    citations: list[AnswerCitationResponse]
    missing_information: list[str]
    warnings: list[AnswerWarningResponse]
    version_conflicts: list[AnswerVersionConflictResponse]
    prompt_version: Literal["v0", "v1"]
    prompt_revision: str
    provider_name: str
    model_name: str
    input_tokens: int | None
    output_tokens: int | None
