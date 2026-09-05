"""文档解析契约与实现。"""

from app.services.parsing.contracts import (
    DocumentParseError,
    ParsedDocument,
    ParsedSection,
    ParseFailureCode,
    ParseWarning,
    TextExtractionStatus,
)
from app.services.parsing.registry import (
    DocumentParser,
    ParserRegistry,
    create_default_parser_registry,
)

__all__ = [
    "DocumentParseError",
    "DocumentParser",
    "ParseFailureCode",
    "ParseWarning",
    "ParsedDocument",
    "ParsedSection",
    "ParserRegistry",
    "TextExtractionStatus",
    "create_default_parser_registry",
]
