"""候选集重排契约与确定性实现。

重排器只接收已经经过知识库、有效期和服务端权限 SQL 过滤的候选，不能把它
当成第二套鉴权。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from app.services.keywording import tokenize
from app.services.query_rewriting import QueryRewriteResult

if TYPE_CHECKING:
    from app.services.retrieval import HybridSearchHit


class CandidateReranker(Protocol):
    """可替换的候选重排接口。"""

    name: str

    def rerank(
        self,
        hits: Sequence[HybridSearchHit],
        *,
        query: QueryRewriteResult,
    ) -> tuple[HybridSearchHit, ...]: ...


class DeterministicCandidateReranker:
    """用词项覆盖率和原始 RRF 做确定性排序。

    该实现不是语义模型，不替代 cross-encoder。
    """

    name = "deterministic_lexical_v1"

    def rerank(
        self,
        hits: Sequence[HybridSearchHit],
        *,
        query: QueryRewriteResult,
    ) -> tuple[HybridSearchHit, ...]:
        query_terms = set(tokenize(query.search_query))
        scored: list[tuple[float, HybridSearchHit]] = []
        for hit in hits:
            content_terms = set(tokenize(hit.content))
            overlap = len(query_terms & content_terms) / max(len(query_terms), 1)
            # RRF 仅作为同等词项覆盖率下的稳定 tie-breaker。
            score = overlap + hit.rrf_score / 100.0
            scored.append((score, replace(hit, rerank_score=score)))
        scored.sort(
            key=lambda item: (
                -item[0],
                -item[1].rrf_score,
                item[1].document_id,
                item[1].section_path,
                item[1].content,
            )
        )
        return tuple(hit for _, hit in scored)
