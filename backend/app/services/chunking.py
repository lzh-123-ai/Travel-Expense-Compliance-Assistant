from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.services.parsing import ParsedDocument, ParsedSection


@dataclass(frozen=True)
class ChunkingConfig:
    max_characters: int = 800
    overlap_characters: int = 100
    strategy_name: str = "heading_page_v1"
    extraction_method: str = "native_text"

    def __post_init__(self) -> None:
        if self.max_characters < 100:
            raise ValueError("Chunk size must be at least 100 characters")
        if self.overlap_characters < 0:
            raise ValueError("Chunk overlap must not be negative")
        if self.overlap_characters >= self.max_characters:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if not self.strategy_name.strip():
            raise ValueError("Chunking strategy name must not be empty")
        if not self.extraction_method.strip():
            raise ValueError("Extraction method must not be empty")


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    content: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    char_count: int
    token_estimate: int
    content_hash: str
    extraction_method: str
    chunking_strategy: str
    chunk_size: int
    chunk_overlap: int


class DeterministicChunker:
    """按章节/连续页合并，超长正文用稳定窗口拆分。"""

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk(self, parsed: ParsedDocument) -> tuple[ChunkDraft, ...]:
        groups: list[tuple[str, int | None, int | None, tuple[str, ...]]] = []
        current_text = ""
        current_page_start: int | None = None
        current_page_end: int | None = None
        current_path: tuple[str, ...] = ()

        def flush() -> None:
            nonlocal current_text, current_page_start, current_page_end, current_path
            if current_text:
                groups.append((current_text, current_page_start, current_page_end, current_path))
            current_text = ""
            current_page_start = None
            current_page_end = None
            current_path = ()

        for section in parsed.sections:
            if len(section.text) > self.config.max_characters:
                flush()
                groups.extend(self._split_section(section))
                continue

            candidate = section.text if not current_text else f"{current_text}\n\n{section.text}"
            if current_text and (
                section.heading_path != current_path
                or not _pages_are_contiguous(current_page_end, section.page_number)
                or len(candidate) > self.config.max_characters
            ):
                flush()
                candidate = section.text

            if not current_text:
                current_page_start = section.page_number
                current_path = section.heading_path
            current_text = candidate
            if section.page_number is not None:
                current_page_end = section.page_number

        flush()
        return tuple(
            self._draft(ordinal, content, page_start, page_end, path)
            for ordinal, (content, page_start, page_end, path) in enumerate(groups)
        )

    def _split_section(
        self, section: ParsedSection
    ) -> list[tuple[str, int | None, int | None, tuple[str, ...]]]:
        windows: list[tuple[str, int | None, int | None, tuple[str, ...]]] = []
        text = section.text
        start = 0
        while start < len(text):
            end = min(start + self.config.max_characters, len(text))
            if end < len(text):
                end = _preferred_break(text, start, end)
            content = text[start:end].strip()
            if content:
                windows.append(
                    (content, section.page_number, section.page_number, section.heading_path)
                )
            if end >= len(text):
                break
            next_start = max(start + 1, end - self.config.overlap_characters)
            while next_start < len(text) and text[next_start].isspace():
                next_start += 1
            start = next_start
        return windows

    def _draft(
        self,
        ordinal: int,
        content: str,
        page_start: int | None,
        page_end: int | None,
        path: tuple[str, ...],
    ) -> ChunkDraft:
        return ChunkDraft(
            ordinal=ordinal,
            content=content,
            page_start=page_start,
            page_end=page_end,
            section_path=path,
            char_count=len(content),
            # 中文平均每个字符常接近一个 token；这里只做无分词器的保守容量估计。
            token_estimate=max(1, len(content)),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            extraction_method=self.config.extraction_method,
            chunking_strategy=self.config.strategy_name,
            chunk_size=self.config.max_characters,
            chunk_overlap=self.config.overlap_characters,
        )


def _pages_are_contiguous(previous: int | None, current: int | None) -> bool:
    if previous is None and current is None:
        return True
    if previous is None or current is None:
        return False
    return current in {previous, previous + 1}


def _preferred_break(text: str, start: int, hard_end: int) -> int:
    lower_bound = start + int((hard_end - start) * 0.6)
    for marker in ("\n\n", "。", "！", "？", ";", "；", ". "):
        position = text.rfind(marker, lower_bound, hard_end)
        if position >= lower_bound:
            return position + len(marker)
    return hard_end
