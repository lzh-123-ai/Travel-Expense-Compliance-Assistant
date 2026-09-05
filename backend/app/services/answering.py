"""有据回答编排与模型输出信任边界。

本服务从混合检索获取已经过滤的证据，构建版本化 Prompt，调用可替换的
Provider，并拒绝模型不可能获得的引用。Route 只负责将结果转换为 HTTP 响应。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import estimated_model_cost, trace_stage
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
    """回答模型 Provider 必须返回的严格结构化输出。"""
    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "refused", "needs_clarification"]
    answer: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    @field_validator("citation_ids")
    @classmethod
    def validate_citation_ids(cls, values: list[str]) -> list[str]:
        """只允许唯一的本次 Prompt 来源 ID，不接受任意 URL 或 UUID。"""
        if len(values) != len(set(values)):
            raise ValueError("Citation IDs must be unique")
        if any(not value.startswith("S") or not value[1:].isdigit() for value in values):
            raise ValueError("Citation IDs must use the S<number> format")
        return values

    @model_validator(mode="after")
    def validate_decision(self) -> AnswerDraft:
        """让每种回答决策都足够明确，便于 API 直接处理。"""
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
    """模型输出未通过回答边界校验。"""


_CURRENT_POLICY_QUESTION_TERMS = ("流程", "步骤", "如何报销", "怎么报销", "怎么办理")
_HISTORICAL_POLICY_PATTERN = re.compile(r"(?:19|20)\d{2}\s*年|历史|以前|当时|旧版|版本")


def _can_use_current_policy(question: str) -> bool:
    """仅允许通用流程题使用服务端当前日期，绝不替费用适用性问题猜日期。"""
    normalized = question.replace(" ", "")
    return (
        not _HISTORICAL_POLICY_PATTERN.search(normalized)
        and any(term in normalized for term in _CURRENT_POLICY_QUESTION_TERMS)
    )


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
        """只基于已检索证据回答，否则返回确定性结果。

        涉及费用适用性的问题必须由用户提供费用日期；通用流程问题则查询系统
        当前有效制度，避免要求用户为“报销流程是什么”虚构一笔费用日期。
        """
        date_source = "expense_date"
        if expense_date is None:
            if not _can_use_current_policy(question):
                # 检索前避免消耗模型资源，也避免不安全地猜测制度版本。
                return _deterministic_result(
                    answer_provider=answer_provider,
                    prompt_version=prompt_version,
                    status="needs_clarification",
                    answer="请先提供费用发生日期，以便确定适用的制度版本。",
                    missing_information=("expense_date",),
                )
            expense_date = date.today()
            date_source = "current_policy"

        # 检索在 SQL 中执行权限/日期过滤；Prompt 文本不是访问控制，不能替代它。
        with trace_stage("retrieval", strategy="hybrid", top_k=top_k) as retrieval_trace:
            retrieval = await self.retriever.search(
                session,
                embedding_provider,
                knowledge_base_id=knowledge_base_id,
                query=question,
                expense_date=expense_date,
                allowed_scopes=allowed_scopes,
                top_k=top_k,
            )
            retrieval_trace["hit_count"] = len(retrieval.hits)
            retrieval_trace["strategy"] = retrieval.strategy
            retrieval_trace["query_rewritten"] = retrieval.query_rewrite is not None
            retrieval_trace["reranked"] = retrieval.reranked
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
            date_source=date_source,
            evidence=evidence,
            version_conflicts=retrieval.version_conflicts,
        )
        with trace_stage(
            "model",
            provider=getattr(answer_provider, "provider_name", "unknown"),
            model=getattr(answer_provider, "model_name", "unknown"),
        ) as model_trace:
            generated = await answer_provider.generate_answer(prompt)
            model_trace["input_tokens"] = generated.usage.input_tokens
            model_trace["output_tokens"] = generated.usage.output_tokens
            model_trace["estimated_cost_usd"] = estimated_model_cost(
                generated.usage.input_tokens,
                generated.usage.output_tokens,
            )
        # 引用 ID 是单次请求的白名单；模型不能虚构来源或引用未进入 Prompt 的切片。
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
    """显式返回证据限制，不在后台静默篡改回答正文。"""
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
    """为追问和无证据拒答路径构建不调用模型的确定性结果。"""
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
