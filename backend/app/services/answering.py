from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.answer_prompts import (
    AnswerEvidence,
    AnswerPrompt,
    PromptVersion,
    build_answer_evidence,
    build_answer_prompt,
)
from app.services.embeddings import EmbeddingProvider
from app.services.retrieval import HybridRetrievalService, VersionConflict


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "refused", "needs_clarification"]
    answer: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    @field_validator("citation_ids")
    @classmethod
    def validate_citation_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Citation IDs must be unique")
        if any(not value.startswith("S") or not value[1:].isdigit() for value in values):
            raise ValueError("Citation IDs must use the S<number> format")
        return values

    @model_validator(mode="after")
    def validate_decision(self) -> AnswerDraft:
        if self.status == "answered" and not self.citation_ids:
            raise ValueError("Answered output requires at least one citation")
        if self.status == "needs_clarification" and not self.missing_information:
            raise ValueError("Clarification output must name missing information")
        return self


@dataclass(frozen=True)
class GenerationUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class GeneratedAnswer:
    draft: AnswerDraft
    usage: GenerationUsage = GenerationUsage()


class AnswerProvider(Protocol):
    provider_name: str
    model_name: str

    async def generate_answer(self, prompt: AnswerPrompt) -> GeneratedAnswer: ...


@dataclass(frozen=True)
class AnswerCitation:
    source_id: str
    chunk_id: UUID
    document_id: UUID
    original_filename: str
    version_label: str | None
    content: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]


@dataclass(frozen=True)
class AnswerWarning:
    code: Literal["partial_text_extraction", "version_conflict"]
    message: str
    document_id: UUID | None = None


@dataclass(frozen=True)
class AnswerResult:
    status: Literal["answered", "refused", "needs_clarification"]
    answer: str
    citations: tuple[AnswerCitation, ...]
    missing_information: tuple[str, ...]
    warnings: tuple[AnswerWarning, ...]
    version_conflicts: tuple[VersionConflict, ...]
    prompt_version: PromptVersion
    provider_name: str
    model_name: str
    usage: GenerationUsage
    prompt_revision: str = "deterministic"
    evidence: tuple[AnswerEvidence, ...] = ()


class AnswerOutputError(RuntimeError):
    """The model returned output that cannot be trusted by the answer boundary."""


class AnswerService:
    def __init__(self, retriever: HybridRetrievalService | None = None) -> None:
        self.retriever = retriever or HybridRetrievalService()

    async def answer(
        self,
        session: AsyncSession,
        embedding_provider: EmbeddingProvider,
        answer_provider: AnswerProvider,
        *,
        knowledge_base_id: UUID,
        question: str,
        expense_date: date | None,
        allowed_scopes: frozenset[str],
        top_k: int = 5,
        prompt_version: PromptVersion = "v1",
    ) -> AnswerResult:
        if expense_date is None:
            return _deterministic_result(
                answer_provider=answer_provider,
                prompt_version=prompt_version,
                status="needs_clarification",
                answer="请先提供费用发生日期，以便确定适用的制度版本。",
                missing_information=("expense_date",),
            )

        retrieval = await self.retriever.search(
            session,
            embedding_provider,
            knowledge_base_id=knowledge_base_id,
            query=question,
            expense_date=expense_date,
            allowed_scopes=allowed_scopes,
            top_k=top_k,
        )
        if not retrieval.hits:
            return _deterministic_result(
                answer_provider=answer_provider,
                prompt_version=prompt_version,
                status="refused",
                answer="当前可访问知识库中没有足够证据回答该问题。",
            )

        evidence = build_answer_evidence(retrieval.hits)
        prompt = build_answer_prompt(
            version=prompt_version,
            question=question,
            expense_date=expense_date,
            evidence=evidence,
            version_conflicts=retrieval.version_conflicts,
        )
        generated = await answer_provider.generate_answer(prompt)
        evidence_by_id = {item.source_id: item for item in evidence}
        unknown_ids = set(generated.draft.citation_ids) - set(evidence_by_id)
        if unknown_ids:
            raise AnswerOutputError(
                "Model cited sources that were not provided: " + ", ".join(sorted(unknown_ids))
            )

        cited_evidence = tuple(
            evidence_by_id[source_id] for source_id in generated.draft.citation_ids
        )
        citations = tuple(
            AnswerCitation(
                source_id=item.source_id,
                chunk_id=item.hit.chunk_id,
                document_id=item.hit.document_id,
                original_filename=item.hit.original_filename,
                version_label=item.hit.version_label,
                content=item.hit.content,
                page_start=item.hit.page_start,
                page_end=item.hit.page_end,
                section_path=item.hit.section_path,
            )
            for item in cited_evidence
        )
        warnings = _build_warnings(cited_evidence, retrieval.version_conflicts)
        return AnswerResult(
            status=generated.draft.status,
            answer=generated.draft.answer,
            citations=citations,
            missing_information=tuple(generated.draft.missing_information),
            warnings=warnings,
            version_conflicts=retrieval.version_conflicts,
            prompt_version=prompt.version,
            provider_name=answer_provider.provider_name,
            model_name=answer_provider.model_name,
            usage=generated.usage,
            prompt_revision=prompt.revision,
            evidence=evidence,
        )


def _build_warnings(
    evidence: tuple[AnswerEvidence, ...],
    conflicts: tuple[VersionConflict, ...],
) -> tuple[AnswerWarning, ...]:
    warnings: list[AnswerWarning] = []
    warned_documents: set[UUID] = set()
    for item in evidence:
        hit = item.hit
        if (hit.text_extraction_status == "partial" or hit.needs_ocr) and (
            hit.document_id not in warned_documents
        ):
            warnings.append(
                AnswerWarning(
                    code="partial_text_extraction",
                    message="该来源存在尚未提取的图片或扫描内容，回答仅依据已提取文本。",
                    document_id=hit.document_id,
                )
            )
            warned_documents.add(hit.document_id)
    if conflicts:
        warnings.append(
            AnswerWarning(
                code="version_conflict",
                message="检索结果包含同类制度的多个版本，请核对费用日期和适用版本。",
            )
        )
    return tuple(warnings)


def _deterministic_result(
    *,
    answer_provider: AnswerProvider,
    prompt_version: PromptVersion,
    status: Literal["refused", "needs_clarification"],
    answer: str,
    missing_information: tuple[str, ...] = (),
) -> AnswerResult:
    return AnswerResult(
        status=status,
        answer=answer,
        citations=(),
        missing_information=missing_information,
        warnings=(),
        version_conflicts=(),
        prompt_version=prompt_version,
        provider_name=answer_provider.provider_name,
        model_name=answer_provider.model_name,
        usage=GenerationUsage(),
    )
