from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from zipfile import BadZipFile, ZipFile

from app.services.storage import StorageService


class DocumentValidationError(Exception):
    pass


class UnsupportedDocumentTypeError(DocumentValidationError):
    pass


class InvalidFilenameError(DocumentValidationError):
    pass


class InvalidDocumentContentError(DocumentValidationError):
    pass


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "markdown"
    TEXT = "text"


@dataclass(frozen=True)
class ValidatedUploadMetadata:
    filename: str
    extension: str
    content_type: str
    document_format: DocumentFormat


@dataclass(frozen=True)
class FormatRule:
    document_format: DocumentFormat
    content_types: frozenset[str]


FORMAT_RULES = {
    ".pdf": FormatRule(DocumentFormat.PDF, frozenset({"application/pdf"})),
    ".docx": FormatRule(
        DocumentFormat.DOCX,
        frozenset(
            {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        ),
    ),
    ".md": FormatRule(
        DocumentFormat.MARKDOWN,
        frozenset({"text/markdown", "text/plain", "text/x-markdown"}),
    ),
    ".txt": FormatRule(DocumentFormat.TEXT, frozenset({"text/plain"})),
}


def validate_upload_metadata(
    filename: str | None,
    content_type: str | None,
) -> ValidatedUploadMetadata:
    raw_filename = filename or ""
    # 同时处理浏览器可能传来的 Windows 路径和恶意 POSIX 相对路径。
    safe_filename = PurePosixPath(PureWindowsPath(raw_filename).name).name
    if not safe_filename or safe_filename in {".", ".."}:
        raise InvalidFilenameError("A document filename is required")
    if len(safe_filename) > 255:
        raise InvalidFilenameError("Filename must be at most 255 characters")

    extension = PurePosixPath(safe_filename).suffix.lower()
    rule = FORMAT_RULES.get(extension)
    if rule is None:
        supported = ", ".join(FORMAT_RULES)
        raise UnsupportedDocumentTypeError(f"Unsupported document extension; allowed: {supported}")

    normalized_content_type = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
    if normalized_content_type not in rule.content_types:
        raise UnsupportedDocumentTypeError(
            f"Content type {normalized_content_type or '<missing>'} does not match {extension}"
        )

    return ValidatedUploadMetadata(
        filename=safe_filename,
        extension=extension,
        content_type=normalized_content_type,
        document_format=rule.document_format,
    )


def validate_stored_content(
    storage: StorageService,
    key: str,
    document_format: DocumentFormat,
    max_uncompressed_size: int,
) -> None:
    if document_format is DocumentFormat.PDF:
        _validate_pdf(storage, key)
    elif document_format is DocumentFormat.DOCX:
        _validate_docx(storage, key, max_uncompressed_size)
    else:
        _validate_utf8_text(storage, key)


def _validate_pdf(storage: StorageService, key: str) -> None:
    with storage.open(key) as document:
        if document.read(5) != b"%PDF-":
            raise InvalidDocumentContentError("File content is not a PDF document")
        document.seek(0, 2)
        size = document.tell()
        document.seek(max(0, size - 2048))
        if b"%%EOF" not in document.read():
            raise InvalidDocumentContentError("PDF end marker is missing")


def _validate_docx(storage: StorageService, key: str, max_uncompressed_size: int) -> None:
    try:
        with storage.open(key) as document, ZipFile(document) as archive:
            entries = archive.infolist()
            if len(entries) > 10_000:
                raise InvalidDocumentContentError("DOCX contains too many archive entries")
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise InvalidDocumentContentError("Encrypted DOCX documents are not supported")
            if sum(entry.file_size for entry in entries) > max_uncompressed_size:
                raise InvalidDocumentContentError("DOCX uncompressed content exceeds the limit")
            names = {entry.filename for entry in entries}
            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(names):
                raise InvalidDocumentContentError("File is a ZIP archive but not a DOCX document")
            if archive.testzip() is not None:
                raise InvalidDocumentContentError("DOCX archive contaiSH a corrupt entry")
    except BadZipFile as exc:
        raise InvalidDocumentContentError("File content is not a valid DOCX document") from exc


def _validate_utf8_text(storage: StorageService, key: str) -> None:
    with storage.open(key) as document:
        content = document.read()
    if not content:
        raise InvalidDocumentContentError("Text document must not be empty")
    if b"\x00" in content:
        raise InvalidDocumentContentError("Text document contains binary null bytes")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentContentError("Text document must use UTF-8 encoding") from exc
