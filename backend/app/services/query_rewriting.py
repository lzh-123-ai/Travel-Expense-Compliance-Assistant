"""受约束的检索查询规范化。

本模块只生成检索副本，不改写用户原问题，也不生成身份、知识库、权限或制度
有效期条件。服务端传入的费用日期仍是唯一的日期事实来源。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

_SPACE_RE = re.compile(r"\s+")
_NEGATION_RE = re.compile(
    r"(?:不包括|不含|不包含|不得|不能|不允许|无需|不需要|除外)[^，,。；;！!？?]{0,24}"
)

# 只补充高频领域同义词，不删除用户原词，避免把约束改成另一种含义。
_SYNONYM_EXPANSIONS: tuple[tuple[str, str], ...] = (
    ("住宿费", "酒店住宿"),
    ("酒店", "住宿费"),
    ("餐费", "餐补"),
    ("飞机票", "机票"),
    ("高铁", "高铁动车"),
    ("火车票", "铁路交通"),
    ("发票", "票据"),
)


@dataclass(frozen=True)
class QueryConstraints:
    """从原问题中保留的业务约束快照。

    这些字段用于审计改写是否越界，不替代服务端鉴权或日期过滤。
    人员字段不能由查询重写推断出数据库身份。
    """

    expense_date: date | None
    locations: tuple[str, ...]
    expense_types: tuple[str, ...]
    persons: tuple[str, ...]
    # 这是服务端鉴权结果的只读快照，不允许从用户问题推断或扩大权限范围。
    employee_scope: tuple[str, ...]
    negations: tuple[str, ...]


@dataclass(frozen=True)
class QueryRewriteResult:
    """保留原问题的检索副本和可检查的约束。"""

    original_query: str
    search_query: str
    constraints: QueryConstraints
    changed: bool
    method: str = "deterministic_v1"


class ConstrainedQueryRewriter:
    """执行不依赖外部模型的保守查询规范化。

    规则改写保持结果可复现；若替换为模型改写，仍须在输出后执行相同约束校验。
    """

    def rewrite(
        self,
        question: str,
        *,
        expense_date: date | None,
        allowed_scopes: frozenset[str] = frozenset(),
    ) -> QueryRewriteResult:
        original = question
        normalized = _normalize_text(question)
        # 否定短语中的词不能触发同义扩展，否则 PostgreSQL 的 OR 查询可能把
        # “不含餐费”扩大成“餐费 餐补”，反而污染候选集。
        non_negated_text = _NEGATION_RE.sub("", normalized)
        expansions = [
            canonical
            for source, canonical in _SYNONYM_EXPANSIONS
            if source in non_negated_text and canonical not in normalized
        ]
        # 原始字符串作为第一段保留，扩展词只能追加，不能覆盖用户约束。
        search_query = " ".join((original.strip(), *expansions)).strip()
        constraints = QueryConstraints(
            expense_date=expense_date,
            locations=_extract_terms(normalized, ("北京", "上海", "广州", "深圳", "杭州")),
            expense_types=_extract_terms(
                normalized,
                ("住宿费", "酒店", "餐费", "餐补", "交通费", "机票", "高铁", "火车票", "发票"),
            ),
            persons=_extract_terms(
                normalized,
                ("本人", "员工", "出差人员", "财务人员", "管理人员"),
            ),
            employee_scope=tuple(sorted(allowed_scopes)),
            negations=tuple(match.group(0) for match in _NEGATION_RE.finditer(normalized)),
        )
        return QueryRewriteResult(
            original_query=original,
            search_query=search_query,
            constraints=constraints,
            changed=search_query != original,
        )


def _normalize_text(value: str) -> str:
    """只统一 Unicode 和空白，不删除日期、地点、金额或否定词。"""
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _extract_terms(value: str, candidates: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(term for term in candidates if term in value)
