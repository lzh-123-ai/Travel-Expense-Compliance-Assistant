import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.main import app
from app.models.document import Document
from app.services.ocr import (
    OCRPageResult,
    OCRProcessingService,
    OCRTextLine,
    PaddleOCRProvider,
    coerce_paddle_result,
    get_ocr_processing_service,
)
from app.services.parsing import ParsedDocument, ParsedSection, TextExtractionStatus
from app.services.storage import LocalStorage, get_storage_service

DOCUMENT_ID = UUID("7e7a8b0e-0af1-4c39-b1b8-cf0c8c2ecf9c")
KNOWLEDGE_BASE_ID = UUID("d5cbd4a3-c5f4-4f72-a98f-fc9a4d2ac187")


class FakeRenderer:
    def __init__(self, pages: dict[int, bytes]) -> None:
        self.pages = pages

    def render_pages(self, source, page_numbers):
        return {page: self.pages[page] for page in page_numbers if page in self.pages}


class FakeProvider:
    provider_name = "fake-ocr"
    model_version = "fake-v1"

    def __init__(self, results: dict[int, OCRPageResult]) -> None:
        self.results = results

    def recognize(self, image: bytes, *, page_number: int) -> OCRPageResult:
        return self.results[page_number]


def make_document(tmp_path: Path) -> tuple[Document, LocalStorage]:
    document = Document(
        id=DOCUMENT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        original_filename="scan.pdf",
        content_type="application/pdf",
        file_size=12,
        storage_key=f"documents/{DOCUMENT_ID}.pdf",
        sha256="b" * 64,
    )
    document.status = "failed"
    document.access_scope = "all_employees"
    document.embedded_image_count = 1
    document.image_only_page_count = 1
    document.text_extraction_status = "no_text"
    document.parse_warnings = []
    document.needs_ocr = True
    destination = tmp_path / document.storage_key
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"%PDF-test")
    return document, LocalStorage(tmp_path)


def make_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = MagicMock()
    return session


def parsed_pdf(
    *, sections: tuple[ParsedSection, ...], image_pages: tuple[int, ...]
) -> ParsedDocument:
    return ParsedDocument(
        sections=sections,
        text_extraction_status=(
            TextExtractionStatus.PARTIAL if sections else TextExtractionStatus.NO_TEXT
        ),
        image_only_page_numbers=image_pages,
        page_count=max(image_pages or (1,)),
        requires_ocr=bool(image_pages),
        parser_name="fixture",
    )


@pytest.mark.asyncio
async def test_ocr_merges_native_and_ocr_pages_with_trace_metadata(tmp_path: Path) -> None:
    document, storage = make_document(tmp_path)
    registry = MagicMock()
    registry.parse.return_value = parsed_pdf(
        sections=(ParsedSection(order=0, text="第一页原生制度", page_number=1),),
        image_pages=(2,),
    )
    result = OCRPageResult(
        page_number=2,
        lines=(OCRTextLine("第二页扫描制度", 0.97, ((1.0, 2.0), (3.0, 4.0))),),
        provider="fake-ocr",
        model_version="fake-v1",
    )
    session = make_session()

    processed = await OCRProcessingService(
        registry=registry,
        renderer=FakeRenderer({2: b"png"}),
        provider=FakeProvider({2: result}),
    ).process(document, session, storage)

    assert processed.status == "ready"
    assert processed.text_extraction_status == "complete"
    assert processed.needs_ocr is False
    assert processed.ocr_status == "completed"
    assert processed.ocr_provider == "fake-ocr"
    chunks = session.add_all.call_args.args[0]
    assert [chunk.extraction_method for chunk in chunks] == ["native_text", "ocr"]
    assert chunks[1].source_metadata["ocr"]["model_version"] == "fake-v1"
    assert chunks[1].source_metadata["ocr"]["lines"][0]["confidence"] == 0.97
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_low_confidence_page_stays_partial_and_is_not_indexed(tmp_path: Path) -> None:
    document, storage = make_document(tmp_path)
    registry = MagicMock()
    registry.parse.return_value = parsed_pdf(
        sections=(ParsedSection(order=0, text="可确认的原生制度", page_number=1),),
        image_pages=(2,),
    )
    low_result = OCRPageResult(
        page_number=2,
        lines=(OCRTextLine("不确定的扫描文字", 0.41),),
        provider="fake-ocr",
        model_version="fake-v1",
    )
    session = make_session()

    await OCRProcessingService(
        registry=registry,
        renderer=FakeRenderer({2: b"png"}),
        provider=FakeProvider({2: low_result}),
    ).process(document, session, storage)

    assert document.status == "ready"
    assert document.text_extraction_status == "partial"
    assert document.needs_ocr is True
    assert document.ocr_status == "partial"
    assert document.ocr_low_confidence_page_count == 1
    chunks = session.add_all.call_args.args[0]
    assert len(chunks) == 1
    assert chunks[0].extraction_method == "native_text"
    assert any(item["code"] == "ocr_low_confidence" for item in document.parse_warnings)


@pytest.mark.asyncio
async def test_pure_scan_pdf_can_create_ocr_chunks(tmp_path: Path) -> None:
    document, storage = make_document(tmp_path)
    registry = MagicMock()
    registry.parse.return_value = parsed_pdf(sections=(), image_pages=(1,))
    result = OCRPageResult(
        page_number=1,
        lines=(OCRTextLine("纯图片页面的制度文字", 0.99),),
        provider="fake-ocr",
        model_version="fake-v1",
    )
    session = make_session()

    await OCRProcessingService(
        registry=registry,
        renderer=FakeRenderer({1: b"png"}),
        provider=FakeProvider({1: result}),
    ).process(document, session, storage)

    assert document.status == "ready"
    assert document.text_extraction_status == "complete"
    assert document.needs_ocr is False
    chunks = session.add_all.call_args.args[0]
    assert len(chunks) == 1
    assert chunks[0].extraction_method == "ocr"
    assert chunks[0].page_start == chunks[0].page_end == 1


def test_coerce_paddle_v3_result_keeps_text_score_and_box() -> None:
    result = coerce_paddle_result(
        {
            "res": {
                "rec_texts": ["住宿费上限"],
                "rec_scores": [0.93],
                "rec_boxes": [[[1, 2], [3, 2], [3, 4], [1, 4]]],
            }
        },
        page_number=3,
    )

    assert result.text == "住宿费上限"
    assert result.min_confidence == 0.93
    assert result.lines[0].bbox == ((1.0, 2.0), (3.0, 2.0), (3.0, 4.0), (1.0, 4.0))


def test_coerce_paddle_result_object_from_predict_generator() -> None:
    class ResultObject:
        def json(self) -> str:
            return '{"res": {"rec_texts": ["扫描页"], "rec_scores": [0.91]}}'

    result = coerce_paddle_result(iter([ResultObject()]), page_number=1)

    assert result.text == "扫描页"
    assert result.min_confidence == 0.91


def test_coerce_paddle_result_accepts_numpy_arrays() -> None:
    numpy = pytest.importorskip("numpy")
    result = coerce_paddle_result(
        {
            "res": {
                "rec_texts": numpy.array(["差旅制度"]),
                "rec_scores": numpy.array([0.96]),
                "rec_boxes": numpy.array([[[1, 2], [3, 2], [3, 4], [1, 4]]]),
            }
        },
        page_number=1,
    )

    assert result.text == "差旅制度"
    assert result.min_confidence == 0.96


def test_paddle_provider_uses_declared_model_and_stable_cpu_backend(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))

    provider = PaddleOCRProvider(lang="ch")
    provider._load_engine()

    assert captured["ocr_version"] == provider.model_version
    assert captured["enable_mkldnn"] is False
    assert captured["use_doc_orientation_classify"] is False


def test_ocr_route_uses_locked_document_and_injected_service(tmp_path: Path) -> None:
    document, storage = make_document(tmp_path)
    document.created_at = document.updated_at = datetime.now(UTC)
    session = make_session()
    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result
    processor = AsyncMock()
    processor.process.return_value = document

    async def override_session():
        yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_ocr_processing_service] = lambda: processor
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/ocr"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    processor.process.assert_awaited_once_with(document, session, storage)
