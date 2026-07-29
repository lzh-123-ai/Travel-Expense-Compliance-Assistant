from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.services.document_validation import (
    DocumentFormat,
    InvalidDocumentContentError,
    validate_stored_content,
    validate_upload_metadata,
)
from app.services.storage import LocalStorage
from tests.sample_documents import make_minimal_docx, make_minimal_pdf


@pytest.mark.parametrize(
    ("filename", "content_type", "content", "expected_format"),
    [
        ("travel-policy.pdf", "application/pdf", make_minimal_pdf(), DocumentFormat.PDF),
        (
            "travel-policy.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            make_minimal_docx(),
            DocumentFormat.DOCX,
        ),
        ("travel-policy.md", "text/markdown", b"# Travel policy\n", DocumentFormat.MARKDOWN),
        ("travel-policy.txt", "text/plain", b"Hotel limit\n", DocumentFormat.TEXT),
    ],
)
def test_supported_document_formats_pass_content_validation(
    tmp_path: Path,
    filename: str,
    content_type: str,
    content: bytes,
    expected_format: DocumentFormat,
) -> None:
    metadata = validate_upload_metadata(filename, content_type)
    storage = LocalStorage(tmp_path)
    key = f"documents/{filename}"
    destination = tmp_path / key
    destination.parent.mkdir(parents=True)
    destination.write_bytes(content)

    validate_stored_content(storage, key, metadata.document_format, 1024 * 1024)

    assert metadata.document_format is expected_format


def test_docx_validation_rejects_generic_zip(tmp_path: Path) -> None:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("notes.txt", "not a Word document")
    destination = tmp_path / "fake.docx"
    destination.write_bytes(output.getvalue())

    with pytest.raises(InvalidDocumentContentError, match="not a DOCX"):
        validate_stored_content(LocalStorage(tmp_path), "fake.docx", DocumentFormat.DOCX, 1024)


@pytest.mark.parametrize("content", [b"", b"policy\x00data", b"\xff\xfe"])
def test_text_validation_rejects_empty_binary_or_non_utf8_content(
    tmp_path: Path,
    content: bytes,
) -> None:
    destination = tmp_path / "invalid.txt"
    destination.write_bytes(content)

    with pytest.raises(InvalidDocumentContentError):
        validate_stored_content(LocalStorage(tmp_path), "invalid.txt", DocumentFormat.TEXT, 1024)
