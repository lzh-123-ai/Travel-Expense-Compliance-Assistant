from __future__ import annotations

import re
import unicodedata

KEYWORD_TOKENIZER_VERSION = "domain_bigram_v1"

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*|[\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# Domain terms make the index useful for policy phrases while bigrams keep
# unseen Chinese wording searchable without an external dictionary service.
_DOMAIN_TERMS = frozenset(
    {
        "差旅",
        "报销",
        "制度",
        "政策",
        "版本",
        "生效",
        "有效期",
        "住宿",
        "住宿费",
        "酒店",
        "标准",
        "上限",
        "普通城市",
        "省会城市",
        "一线城市",
        "展会",
        "大型展会",
        "临时标准",
        "餐补",
        "交通",
        "高铁动车",
        "二等座",
        "飞机",
        "机票",
        "座席",
        "发票",
        "电子发票",
        "原始票据",
        "行程单",
        "附件",
        "材料",
        "审批",
        "审批顺序",
        "财务",
        "内部",
        "审计",
        "复核",
        "金额",
        "提交",
        "截止",
        "紧急出差",
        "超标准",
        "入住",
        "离店",
        "指定",
    }
)


def _segment_cjk(run: str) -> list[str]:
    terms: list[str] = []
    index = 0
    while index < len(run):
        matches = [term for term in _DOMAIN_TERMS if run.startswith(term, index)]
        if matches:
            term = max(matches, key=len)
            terms.append(term)
            index += len(term)
            continue
        if index + 2 <= len(run):
            terms.append(run[index : index + 2])
        else:
            terms.append(run[index])
        index += 1
    return terms


def tokenize(text: str) -> tuple[str, ...]:
    """Return deterministic space-separated terms for PostgreSQL simple FTS."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    terms: list[str] = []
    for match in _TOKEN_RE.finditer(normalized):
        token = match.group(0)
        if _CJK_RE.search(token):
            terms.extend(_segment_cjk(token))
        else:
            terms.append(token)
    return tuple(term for term in terms if term.strip())


def searchable_text(text: str) -> str:
    return " ".join(tokenize(text))
