"""查询重写与候选重排的四组离线消融契约。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import log2
from typing import Literal

from app.services.retrieval import (
    DenseRetrievalService,
    HybridRetrievalService,
    KeywordRetrievalService,
)

Stage15Variant = Literal[
    "hybrid",
    "hybrid+rewrite",
    "hybrid+rerank",
    "hybrid+rewrite+rerank",
]


@dataclass(frozen=True)
class Stage15AblationConfig:
    """一个变体只打开一个或两个明确因素。"""

    name: Stage15Variant
    enable_query_rewrite: bool
    enable_rerank: bool


ABLATION_CONFIGS: tuple[Stage15AblationConfig, ...] = (
    Stage15AblationConfig("hybrid", False, False),
    Stage15AblationConfig("hybrid+rewrite", True, False),
    Stage15AblationConfig("hybrid+rerank", False, True),
    Stage15AblationConfig("hybrid+rewrite+rerank", True, True),
)


def get_ablation_config(variant: Stage15Variant) -> Stage15AblationConfig:
    for config in ABLATION_CONFIGS:
        if config.name == variant:
            return config
    raise ValueError(f"Unknown Stage 15 variant: {variant}")


def build_retrieval_variant(
    variant: Stage15Variant,
    *,
    dense: DenseRetrievalService | None = None,
    keyword: KeywordRetrievalService | None = None,
) -> HybridRetrievalService:
    """构造隔离的检索评测变体，避免修改默认线上配置。"""
    config = get_ablation_config(variant)
    return HybridRetrievalService(
        dense=dense,
        keyword=keyword,
        enable_query_rewrite=config.enable_query_rewrite,
        enable_rerank=config.enable_rerank,
    )


def ndcg_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: set[str],
    *,
    k: int,
) -> float:
    """按二元相关性计算 NDCG@K，适合当前人工标注的期望来源集合。"""
    if k < 1:
        raise ValueError("k must be positive")
    # 同一制度的多个切片只计算一次，否则重复命中会虚高文档版本级 NDCG。
    ranked = tuple(dict.fromkeys(retrieved_ids))[:k]
    dcg = sum(
        1.0 / log2(rank + 1)
        for rank, item_id in enumerate(ranked, start=1)
        if item_id in relevant_ids
    )
    ideal_hits = min(len(relevant_ids), k)
    if ideal_hits == 0:
        return 0.0
    idcg = sum(1.0 / log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg
