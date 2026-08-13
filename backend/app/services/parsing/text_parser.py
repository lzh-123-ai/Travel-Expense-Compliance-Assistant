"""UTF-8 纯文本和 Markdown 解析器。

Markdown 图片 URL 会被移除但绝不请求。外部内容应进入受控的 OCR/媒体流程，
不能由上传文档解析器直接访问。
"""

from __future__ import annotations

import re
from typing import BinaryIO

from app.services.document_validation import DocumentFormat
from app.services.parsing.contracts import (
    DocumentParseError,
    ParsedDocument,
    ParsedSection,
    ParseFailureCode,
    ParseWarning,
    TextExtractionStatus,
)

MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^\n)]*\)")


def _decode_utf8(source: BinaryIO) -> str:
    try:
        return source.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParseError(
            ParseFailureCode.CORRUPT_DOCUMENT,
            "Text document must use UTF-8 encoding",
        ) from exc


def _plain_sections(content: str) -> tuple[ParsedSection, ...]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    return tuple(
        ParsedSection(order=order, text=paragraph) for order, paragraph in enumerate(paragraphs)
    )


class PlainTextParser:
    document_format = DocumentFormat.TEXT

    def parse(self, source: BinaryIO) -> ParsedDocument:
        sections = _plain_sections(_decode_utf8(source))
        if not sections:
            return ParsedDocument(
                sections=(),
                text_extraction_status=TextExtractionStatus.NO_TEXT,
                warnings=(ParseWarning("empty_document", "Document contains no text"),),
                parser_name="plain_text",
            )
        return ParsedDocument(
            sections=sections,
            text_extraction_status=TextExtractionStatus.COMPLETE,
            parser_name="plain_text",
        )


class MarkdownParser:
    document_format = DocumentFormat.MARKDOWN

    def parse(self, source: BinaryIO) -> ParsedDocument:
        content = _decode_utf8(source)
        image_count = len(MARKDOWN_IMAGE.findall(content))
        # 图片目标可能是 file://、内网地址或恶意 URL；解析器只删除语法，绝不读取目标。
        content_without_images = MARKDOWN_IMAGE.sub("", content)
        sections = self._sections(content_without_images)
        warnings: tuple[ParseWarning, ...] = ()
        if image_count:
            warnings = (
                ParseWarning(
                    "markdown_images_not_fetched",
                    f"Skipped {image_count} Markdown image reference(s)",
                ),
            )
        if not sections:
            return ParsedDocument(
                sections=(),
                text_extraction_status=TextExtractionStatus.NO_TEXT,
                embedded_image_count=image_count,
                warnings=warnings
                + (ParseWarning("empty_document", "Document contains no extractable text"),),
                parser_name="markdown",
            )
        return ParsedDocument(
            sections=sections,
            text_extraction_status=TextExtractionStatus.COMPLETE,
            embedded_image_count=image_count,
            warnings=warnings,
            parser_name="markdown",
        )

    @staticmethod
    def _sections(content: str) -> tuple[ParsedSection, ...]:
        heading_stack: list[str] = []
        current_lines: list[str] = []
        current_path: tuple[str, ...] = ()
        blocks: list[tuple[str, tuple[str, ...]]] = []

        def flush() -> None:
            text = "\n".join(current_lines).strip()
            if text:
                blocks.append((text, current_path))
            current_lines.clear()

        for line in content.splitlines():
            heading = MARKDOWN_HEADING.match(line)
            if heading:
                flush()
                level = len(heading.group(1))
                title = heading.group(2).strip()
                del heading_stack[level - 1 :]
                while len(heading_stack) < level - 1:
                    heading_stack.append("Untitled")
                heading_stack.append(title)
                current_path = tuple(heading_stack)
                current_lines.append(title)
            else:
                current_lines.append(line)
        flush()

        return tuple(
            ParsedSection(order=order, text=text, heading_path=path)
            for order, (text, path) in enumerate(blocks)
        )
