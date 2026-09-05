from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.stage15_ablation import (
    ABLATION_CONFIGS,
    build_retrieval_variant,
    ndcg_at_k,
)
from app.evaluation.stage15_baseline import _field_contract
from app.services.query_rewriting import ConstrainedQueryRewriter
from app.services.reranking import DeterministicCandidateReranker
from app.services.retrieval import (
    DenseRetrievalService,
    DenseSearchHit,
    HybridRetrievalService,
    HybridSearchHit,
    KeywordRetrievalService,
    KeywordSearchHit,
)

KB_ID = UUID("2f1c75f4-bf91-4f40-aee7-54152e57b0f8")
DOC_A = UUID("4a40cf8c-0d67-4e0f-9ef1-1c4d9ec1e7a3")
DOC_B = UUID("5b51df9d-1e70-4a8e-a016-2d4dabd2f9b4")
CHUNK_A = UUID("1056bc33-2194-4965-b148-9bde6180db44")
CHUNK_B = UUID("2167cd44-32a5-4f24-8a2f-b036436c3710")


class FakeProvider:
    provider_name = "fixture"
    model_name = "fixture-v1"
    dimension = 512


def _dense_hit(chunk_id: UUID, document_id: UUID, content: str) -> DenseSearchHit:
    return DenseSearchHit(
        chunk_id=chunk_id,
        document_id=document_id,
        original_filename="policy.md",
        version_label="TRAVEL-2026.1",
        content=content,
        page_start=None,
        page_end=None,
        section_path=("住宿费",),
        cosine_distance=0.2,
    )


def _keyword_hit(chunk_id: UUID, document_id: UUID, content: str) -> KeywordSearchHit:
    return KeywordSearchHit(
        chunk_id=chunk_id,
        document_id=document_id,
        original_filename="policy.md",
        version_label="TRAVEL-2026.1",
        content=content,
        page_start=None,
        page_end=None,
        section_path=("住宿费",),
        keyword_score=0.2,
    )


def _hybrid_hit(
    chunk_id: UUID,
    document_id: UUID,
    content: str,
    rrf_score: float,
) -> HybridSearchHit:
    return HybridSearchHit(
        chunk_id=chunk_id,
        document_id=document_id,
        original_filename="policy.md",
        version_label="TRAVEL-2026.1",
        content=content,
        page_start=None,
        page_end=None,
        section_path=("住宿费",),
        dense_similarity=None,
        keyword_score=None,
        dense_rank=None,
        keyword_rank=None,
        rrf_score=rrf_score,
    )


def test_query_rewrite_keeps_original_and_protected_constraints() -> None:
    question = "请查 2026 年 5 月上海住宿费，不能包含餐费，员工本人适用。"

    result = ConstrainedQueryRewriter().rewrite(
        question,
        expense_date=date(2026, 5, 1),
        allowed_scopes=frozenset({"all_employees"}),
    )

    assert result.original_query == question
    assert question.replace("  ", " ") in result.search_query
    assert result.constraints.expense_date == date(2026, 5, 1)
    assert result.constraints.locations == ("上海",)
    assert "住宿费" in result.constraints.expense_types
    assert result.constraints.persons == ("本人", "员工")
    assert result.constraints.employee_scope == ("all_employees",)
    assert result.constraints.negations == ("不能包含餐费",)
    assert "餐补" not in result.search_query
    assert "身份" not in result.search_query
    assert "知识库" not in result.search_query


def test_reranker_prefers_query_terms_and_is_deterministic() -> None:
    dense = _hybrid_hit(CHUNK_A, DOC_A, "餐费报销材料", 0.03)
    keyword = _hybrid_hit(CHUNK_B, DOC_B, "上海住宿费上限", 0.02)
    # 直接通过融合结果验证排序器，不依赖数据库或 embedding。
    merged = DeterministicCandidateReranker().rerank(
        (dense, keyword),
        query=ConstrainedQueryRewriter().rewrite("上海住宿费", expense_date=date(2026, 5, 1)),
    )

    assert merged[0].chunk_id == CHUNK_B
    assert merged[0].rerank_score is not None


@pytest.mark.asyncio
async def test_rewrite_and_rerank_use_expanded_candidates_after_both_searches() -> None:
    dense = AsyncMock(spec=DenseRetrievalService)
    keyword = AsyncMock(spec=KeywordRetrievalService)
    dense.search.return_value = (_dense_hit(CHUNK_A, DOC_A, "上海住宿费上限"),)
    keyword.search.return_value = (_keyword_hit(CHUNK_B, DOC_B, "上海酒店住宿标准"),)
    service = HybridRetrievalService(
        dense=dense,
        keyword=keyword,
        enable_query_rewrite=True,
        enable_rerank=True,
    )

    result = await service.search(
        AsyncMock(spec=AsyncSession),
        FakeProvider(),
        knowledge_base_id=KB_ID,
        query="上海住宿费",
        expense_date=date(2026, 5, 1),
        allowed_scopes=frozenset({"all_employees"}),
        top_k=1,
    )

    assert result.strategy == "hybrid+rewrite+rerank"
    assert result.query_rewrite is not None
    assert result.query_rewrite.constraints.employee_scope == ("all_employees",)
    assert result.reranked is True
    assert dense.search.await_args.kwargs["query"] != "上海住宿费"
    assert keyword.search.await_args.kwargs["query"] == dense.search.await_args.kwargs["query"]
    assert dense.search.await_args.kwargs["top_k"] == 20
    assert result.hits[0].rerank_score is not None


def test_stage15_has_exactly_four_ablation_variants_and_ndcg() -> None:
    assert [config.name for config in ABLATION_CONFIGS] == [
        "hybrid",
        "hybrid+rewrite",
        "hybrid+rerank",
        "hybrid+rewrite+rerank",
    ]
    assert ndcg_at_k(["a", "b", "c"], {"b", "c"}, k=3) == pytest.approx(
        (1 / 1.584962500721156 + 1 / 2) / (1 + 1 / 1.584962500721156)
    )
    assert ndcg_at_k(["a", "a", "b"], {"a", "b"}, k=3) == pytest.approx(1.0)
    assert build_retrieval_variant("hybrid").enable_rerank is False
    assert build_retrieval_variant("hybrid+rerank").enable_rerank is True


def test_stage15_field_contract_checks_all_protected_terms() -> None:
    question = "上海员工本人住宿费，不包含餐费"
    rewrite = ConstrainedQueryRewriter().rewrite(
        question,
        expense_date=date(2026, 5, 1),
        allowed_scopes=frozenset({"all_employees"}),
    )

    contract = _field_contract(
        SimpleNamespace(
            query=question,
            expense_date=date(2026, 5, 1),
            allowed_scopes=["all_employees"],
        ),
        SimpleNamespace(query_rewrite=rewrite),
    )

    assert contract["checked"] is True
    assert contract["preserved"] is True
    assert contract["employee_scope_preserved"] is True
    assert contract["employee_scope"] == ["all_employees"]
    assert contract["locations"] == ["上海"]
    assert contract["expense_types"] == ["住宿费", "餐费"]
    assert contract["persons"] == ["本人", "员工"]
    assert contract["negations"] == ["不包含餐费"]


def test_stage15_field_contract_rejects_changed_server_scope() -> None:
    """权限范围即使没有进入检索文本，也必须与服务端输入完全一致。"""
    question = "上海住宿费标准"
    rewrite = ConstrainedQueryRewriter().rewrite(
        question,
        expense_date=date(2026, 5, 1),
        allowed_scopes=frozenset({"all_employees"}),
    )

    contract = _field_contract(
        SimpleNamespace(
            query=question,
            expense_date=date(2026, 5, 1),
            allowed_scopes=["finance"],
        ),
        SimpleNamespace(query_rewrite=rewrite),
    )

    assert contract["employee_scope_preserved"] is False
    assert contract["preserved"] is False
