"""所有文件格式解析器共用的、与数据库无关的契约。

解析器在这里返回不可变事实；``DocumentProcessingService`` 再将其映射为
数据库状态和切片。这让格式解析无需数据库或文件系统实现即可测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TextExtractionStatus(StrEnum):
    """文字提取完整性；它不替代 Document 的业务处理状态。"""

    NOT_ATTEMPTED = "not_attempted"
    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_TEXT = "no_text"
    FAILED = "failed"


class ParseFailureCode(StrEnum):
    """可安全保存和返回的解析失败类型，不包含底层异常详情。"""

    CORRUPT_DOCUMENT = "corrupt_document"
    ENCRYPTED_DOCUMENT = "encrypted_document"
    EMPTY_DOCUMENT = "empty_document"
    NO_EXTRACTABLE_TEXT = "no_extractable_text"
    UNSUPPORTED_FORMAT = "unsupported_format"
    PARSER_ERROR = "parser_error"


@dataclass(frozen=True)
class ParseWarning:
    code: str
    message: str
    page_number: int | None = None

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("Parse warning code must not be empty")
        if not self.message.strip():
            raise ValueError("Parse warning message must not be empty")
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("Parse warning page number must be one-based")

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "code": self.code,
            "message": self.message,
            "page_number": self.page_number,
        }


@dataclass(frozen=True)
class ParsedSection:
    """解析器输出的最小可追溯正文单元，还不是最终 chunk。"""

    order: int
    text: str
    page_number: int | None = None
    heading_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.order < 0:
            raise ValueError("Parsed section order must not be negative")
        if not self.text.strip():
            raise ValueError("Parsed section text must not be empty")
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("Parsed section page number must be one-based")
        if any(not heading.strip() for heading in self.heading_path):
            raise ValueError("Heading path must not contain empty headings")


@dataclass(frozen=True)
class ParsedDocument:
    """与数据库无关的解析结果；持久化和状态转换由编排服务负责。"""

    sections: tuple[ParsedSection, ...]
    text_extraction_status: TextExtractionStatus
    embedded_image_count: int = 0
    image_only_page_numbers: tuple[int, ...] = ()
    failed_page_numbers: tuple[int, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()
    page_count: int | None = None
    requires_ocr: bool = False
    parser_name: str = "unknown"
    parser_version: str = "1"

    def __post_init__(self) -> None:
        expected_orders = list(range(len(self.sections)))
        if [section.order for section in self.sections] != expected_orders:
            raise ValueError("Parsed section orders must be continuous and zero-based")
        if self.embedded_image_count < 0:
            raise ValueError("Embedded image count must not be negative")
        if self.page_count is not None and self.page_count < 1:
            raise ValueError("Page count must be positive when present")
        for page_number in (*self.image_only_page_numbers, *self.failed_page_numbers):
            if page_number < 1:
                raise ValueError("Page numbers must be one-based")
            if self.page_count is not None and page_number > self.page_count:
                raise ValueError("Page number exceeds the parsed document page count")
        if self.text_extraction_status is TextExtractionStatus.COMPLETE and not self.sections:
            raise ValueError("Complete extraction requires at least one section")
        if self.text_extraction_status is TextExtractionStatus.PARTIAL:
            if not self.sections:
                raise ValueError("Partial extraction requires extracted sections")
            if not self.image_only_page_numbers and not self.failed_page_numbers:
                raise ValueError("Partial extraction requires at least one unextracted page")
        if self.text_extraction_status is TextExtractionStatus.NO_TEXT and self.sections:
            raise ValueError("No-text extraction must not contain sections")
        if not self.parser_name.strip() or not self.parser_version.strip():
            raise ValueError("Parser name and version must not be empty")

    @property
    def needs_ocr(self) -> bool:
        return self.requires_ocr or bool(self.image_only_page_numbers)

    @property
    def image_only_page_count(self) -> int:
        return len(self.image_only_page_numbers)

    @property
    def character_count(self) -> int:
        return sum(len(section.text) for section in self.sections)

    def serialized_warnings(self) -> list[dict[str, str | int | None]]:
        return [warning.as_dict() for warning in self.warnings]


class DocumentParseError(Exception):
    """解析器向编排层提供的安全、稳定失败语义。"""

    def __init__(self, code: ParseFailureCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
