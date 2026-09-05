"""保留页码溯源与 OCR 警告的 PDF 原生文本解析器。

每个可读取页面都会成为一个区块。纯图片页或提取失败页会标记为部分提取，
交给后续 OCR 流程，绝不静默当作已索引文本。
"""

from __future__ import annotations

import re
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.services.document_validation import DocumentFormat
from app.services.parsing.contracts import (
    DocumentParseError,
    ParsedDocument,
    ParsedSection,
    ParseFailureCode,
    ParseWarning,
    TextExtractionStatus,
)

WHITESPACE = re.compile(r"[ \t]+")


def _normalize_pdf_text(text: str) -> str:
    lines = [WHITESPACE.sub(" ", line).strip() for line in text.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


class PdfParser:
    document_format = DocumentFormat.PDF

    def __init__(self, minimum_text_characters_per_image_page: int = 20) -> None:
        self.minimum_text_characters_per_image_page = minimum_text_characters_per_image_page

    def parse(self, source: BinaryIO) -> ParsedDocument:
        try:
            reader = PdfReader(source, strict=False)
            if reader.is_encrypted:
                raise DocumentParseError(
                    ParseFailureCode.ENCRYPTED_DOCUMENT,
                    "Encrypted PDF documents are not supported",
                )
            page_count = len(reader.pages)
        except DocumentParseError:
            raise
        except (PdfReadError, ValueError, OSError) as exc:
            raise DocumentParseError(
                ParseFailureCode.CORRUPT_DOCUMENT,
                "PDF document could not be parsed",
            ) from exc

        if page_count == 0:
            raise DocumentParseError(
                ParseFailureCode.EMPTY_DOCUMENT,
                "PDF document contains no pages",
            )

        sections: list[ParsedSection] = []
        warnings: list[ParseWarning] = []
        image_only_pages: list[int] = []
        failed_pages: list[int] = []
        embedded_image_count = 0

        for page_number, page in enumerate(reader.pages, start=1):
            try:
                image_count = len(page.images)
            except (
                Exception
            ):  # pypdf 可能拒绝异常图片对象，但页面文字仍然可用。
                image_count = 0
                warnings.append(
                    ParseWarning(
                        "image_inspection_failed",
                        "Could not inspect embedded images on this page",
                        page_number,
                    )
                )
            embedded_image_count += image_count

            try:
                text = _normalize_pdf_text(page.extract_text() or "")
            # 异常图片对象不应阻止仍然可用的文本进入解析结果。
            except Exception:
                failed_pages.append(page_number)
                warnings.append(
                    ParseWarning(
                        "page_text_extraction_failed",
                        "Text extraction failed for this page",
                        page_number,
                    )
                )
                continue

            visible_characters = len(re.sub(r"\s+", "", text))
            if image_count and visible_characters < self.minimum_text_characters_per_image_page:
                image_only_pages.append(page_number)
                warnings.append(
                    ParseWarning(
                        "image_only_page",
                        "Page contains images but no reliable extractable text",
                        page_number,
                    )
                )
                continue
            if not text:
                warnings.append(
                    ParseWarning("blank_page", "Page contains no extractable text", page_number)
                )
                continue
            if image_count:
                warnings.append(
                    ParseWarning(
                        "embedded_images_detected",
                        "Page text was extracted; embedded image content was not interpreted",
                        page_number,
                    )
                )
            sections.append(ParsedSection(order=len(sections), text=text, page_number=page_number))

        if not sections:
            return ParsedDocument(
                sections=(),
                text_extraction_status=TextExtractionStatus.NO_TEXT,
                embedded_image_count=embedded_image_count,
                image_only_page_numbers=tuple(image_only_pages),
                failed_page_numbers=tuple(failed_pages),
                warnings=tuple(warnings),
                page_count=page_count,
                requires_ocr=bool(image_only_pages),
                parser_name="pypdf",
            )

        extraction_status = (
            TextExtractionStatus.PARTIAL
            if image_only_pages or failed_pages
            else TextExtractionStatus.COMPLETE
        )
        return ParsedDocument(
            sections=tuple(sections),
            text_extraction_status=extraction_status,
            embedded_image_count=embedded_image_count,
            image_only_page_numbers=tuple(image_only_pages),
            failed_page_numbers=tuple(failed_pages),
            warnings=tuple(warnings),
            page_count=page_count,
            parser_name="pypdf",
        )
