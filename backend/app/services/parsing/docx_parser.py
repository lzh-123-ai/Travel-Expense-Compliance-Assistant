"""保留标题路径、表格和图片限制的 DOCX 解析器。

DOCX 没有可信的页码概念，因此切片保留标题路径而不是伪造页码。内嵌图片只会
标记为未提取，不会被当作文本。
"""

from __future__ import annotations

import re
from typing import BinaryIO
from zipfile import BadZipFile

from docx import Document as open_docx
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.document_validation import DocumentFormat
from app.services.parsing.contracts import (
    DocumentParseError,
    ParsedDocument,
    ParsedSection,
    ParseFailureCode,
    ParseWarning,
    TextExtractionStatus,
)

HEADING_STYLE = re.compile(r"heading\s*(\d+)", re.IGNORECASE)


def _heading_level(paragraph: Paragraph) -> int | None:
    style = paragraph.style
    for candidate in (style.style_id, style.name):
        match = HEADING_STYLE.search(candidate or "")
        if match:
            return max(1, min(9, int(match.group(1))))
    return None


def _table_text(table: Table) -> str:
    lines: list[str] = []
    for row in table.rows:
        values: list[str] = []
        seen_cells: set[int] = set()
        for cell in row.cells:
            cell_identity = id(cell._tc)
            if cell_identity in seen_cells:
                continue
            seen_cells.add(cell_identity)
            value = " ".join(part.strip() for part in cell.text.splitlines() if part.strip())
            values.append(value)
        line = " | ".join(values).strip(" |")
        if line:
            lines.append(line)
    return "\n".join(lines)


class DocxParser:
    document_format = DocumentFormat.DOCX

    def parse(self, source: BinaryIO) -> ParsedDocument:
        try:
            document = open_docx(source)
        except (PackageNotFoundError, BadZipFile, KeyError, ValueError, OSError) as exc:
            raise DocumentParseError(
                ParseFailureCode.CORRUPT_DOCUMENT,
                "DOCX document could not be parsed",
            ) from exc

        embedded_image_count = len(document.element.xpath(".//*[local-name()='blip']"))
        heading_stack: list[str] = []
        sections: list[ParsedSection] = []

        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if not text:
                    continue
                heading_level = _heading_level(block)
                if heading_level is not None:
                    del heading_stack[heading_level - 1 :]
                    while len(heading_stack) < heading_level - 1:
                        heading_stack.append("Untitled")
                    heading_stack.append(text)
                sections.append(
                    ParsedSection(
                        order=len(sections),
                        text=text,
                        heading_path=tuple(heading_stack),
                    )
                )
            elif isinstance(block, Table):
                text = _table_text(block)
                if text:
                    sections.append(
                        ParsedSection(
                            order=len(sections),
                            text=text,
                            heading_path=tuple(heading_stack),
                        )
                    )

        warnings: tuple[ParseWarning, ...] = ()
        if embedded_image_count:
            warnings = (
                ParseWarning(
                    "embedded_images_detected",
                    f"Detected {embedded_image_count} DOCX image(s); "
                    "image content was not interpreted",
                ),
            )
        if not sections:
            if embedded_image_count:
                warnings += (
                    ParseWarning(
                        "image_only_document",
                        "DOCX contains images but no extractable text",
                    ),
                )
            else:
                warnings += (ParseWarning("empty_document", "DOCX contains no text"),)
            return ParsedDocument(
                sections=(),
                text_extraction_status=TextExtractionStatus.NO_TEXT,
                embedded_image_count=embedded_image_count,
                warnings=warnings,
                requires_ocr=bool(embedded_image_count),
                parser_name="python_docx",
            )

        return ParsedDocument(
            sections=tuple(sections),
            text_extraction_status=TextExtractionStatus.COMPLETE,
            embedded_image_count=embedded_image_count,
            warnings=warnings,
            parser_name="python_docx",
        )
