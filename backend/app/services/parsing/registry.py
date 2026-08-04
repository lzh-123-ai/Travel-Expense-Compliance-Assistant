from __future__ import annotations

from typing import BinaryIO, Protocol

from app.services.document_validation import DocumentFormat
from app.services.parsing.contracts import (
    DocumentParseError,
    ParsedDocument,
    ParseFailureCode,
)


class DocumentParser(Protocol):
    document_format: DocumentFormat

    def parse(self, source: BinaryIO) -> ParsedDocument: ...


class ParserRegistry:
    """按已验证的格式分派解析器，不根据客户端文件名猜测格式。"""

    def __init__(self, parsers: tuple[DocumentParser, ...] = ()) -> None:
        self._parsers: dict[DocumentFormat, DocumentParser] = {}
        for parser in parsers:
            self.register(parser)

    def register(self, parser: DocumentParser) -> None:
        if parser.document_format in self._parsers:
            raise ValueError(f"Parser already registered for {parser.document_format}")
        self._parsers[parser.document_format] = parser

    def parse(self, document_format: DocumentFormat, source: BinaryIO) -> ParsedDocument:
        parser = self._parsers.get(document_format)
        if parser is None:
            raise DocumentParseError(
                ParseFailureCode.UNSUPPORTED_FORMAT,
                f"No parser is registered for {document_format}",
            )
        source.seek(0)
        return parser.parse(source)


def create_default_parser_registry() -> ParserRegistry:
    # 延迟导入让纯契约测试不依赖具体解析库，也避免模块加载时做重工作。
    from app.services.parsing.docx_parser import DocxParser
    from app.services.parsing.pdf_parser import PdfParser
    from app.services.parsing.text_parser import MarkdownParser, PlainTextParser

    return ParserRegistry((PdfParser(), DocxParser(), MarkdownParser(), PlainTextParser()))
