"""扫描版 PDF 的受控 OCR 后处理。

OCR 不进入默认上传解析链路，而由显式接口触发。原生文字仍由 ``PdfParser``
负责；本模块只处理解析器标出的图片页，并把供应商、模型版本和置信度写进
切片溯源元数据。低置信度页面不会悄悄变成可检索证据。
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from threading import Lock
from typing import Any, BinaryIO, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document
from app.services.chunking import DeterministicChunker
from app.services.document_processing import DocumentProcessingService
from app.services.document_validation import (
    DocumentFormat,
    DocumentValidationError,
    validate_upload_metadata,
)
from app.services.parsing import (
    DocumentParseError,
    ParsedDocument,
    ParsedSection,
    ParseWarning,
    TextExtractionStatus,
    create_default_parser_registry,
)
from app.services.parsing.registry import ParserRegistry
from app.services.storage import StorageService

logger = logging.getLogger(__name__)


class OCRProcessingError(Exception):
    """OCR 编排或可选运行时的安全失败。"""


class OCRProviderError(OCRProcessingError):
    """OCR 引擎不可用或返回了无法解释的结果。"""


class OCRUnsupportedDocumentError(OCRProcessingError):
    """OCR 处理链路不支持该文件类型。"""


class OCRNotRequiredError(OCRProcessingError):
    """文档没有待处理的图片页。"""


@dataclass(frozen=True)
class OCRTextLine:
    """一行识别文字及其置信度/位置。"""

    text: str
    confidence: float
    bbox: tuple[tuple[float, float], ...] | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("OCR text must not be empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("OCR confidence must be between 0 and 1")
        if self.bbox is not None and any(len(point) != 2 for point in self.bbox):
            raise ValueError("OCR bounding box points must contain x and y")


@dataclass(frozen=True)
class OCRPageResult:
    """一个 PDF 页面的稳定 OCR 输出契约。"""

    page_number: int
    lines: tuple[OCRTextLine, ...]
    provider: str
    model_version: str

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("OCR page number must be one-based")
        if not self.provider.strip() or not self.model_version.strip():
            raise ValueError("OCR provider and model version must not be empty")

    @property
    def text(self) -> str:
        return "\n".join(line.text.strip() for line in self.lines if line.text.strip())

    @property
    def min_confidence(self) -> float:
        return min((line.confidence for line in self.lines), default=0.0)


class PageImageRenderer(Protocol):
    """把指定 PDF 页渲染为 OCR 引擎可读取的图片。"""

    def render_pages(
        self, source: BinaryIO, page_numbers: tuple[int, ...]
    ) -> Mapping[int, bytes]: ...


class OCRProvider(Protocol):
    """可替换的 OCR 引擎适配器。"""

    provider_name: str
    model_version: str

    def recognize(self, image: bytes, *, page_number: int) -> OCRPageResult: ...


class PypdfiumPageRenderer:
    """使用 pypdfium2 渲染图片页，依赖仅在显式调用 OCR 时导入。"""

    def __init__(self, scale: float = 2.0) -> None:
        if scale <= 0:
            raise ValueError("PDF render scale must be positive")
        self.scale = scale

    def render_pages(self, source: BinaryIO, page_numbers: tuple[int, ...]) -> Mapping[int, bytes]:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise OCRProviderError(
                "PDF OCR rendering is unavailable; install the project's [ocr] extra"
            ) from exc

        try:
            pdf = pdfium.PdfDocument(source.read())
            rendered: dict[int, bytes] = {}
            for page_number in page_numbers:
                if page_number < 1 or page_number > len(pdf):
                    raise OCRProviderError(f"PDF page {page_number} is outside the document")
                page = pdf[page_number - 1]
                bitmap = page.render(scale=self.scale)
                try:
                    image = bitmap.to_pil()
                    output = BytesIO()
                    image.save(output, format="PNG")
                    rendered[page_number] = output.getvalue()
                finally:
                    close = getattr(bitmap, "close", None)
                    if close is not None:
                        close()
                    page_close = getattr(page, "close", None)
                    if page_close is not None:
                        page_close()
            return rendered
        except OCRProviderError:
            raise
        except Exception as exc:
            logger.exception("PDF page rendering failed")
            raise OCRProviderError("PDF pages could not be rendered for OCR") from exc
        finally:
            close = locals().get("pdf")
            if close is not None:
                pdf_close = getattr(close, "close", None)
                if pdf_close is not None:
                    pdf_close()


class PaddleOCRProvider:
    """PaddleOCR 适配器；模型只在第一次真实 OCR 请求时加载。"""

    provider_name = "paddleocr"
    model_version = "PP-OCRv5"

    def __init__(self, lang: str = "ch") -> None:
        self.lang = lang
        self._engine: Any | None = None
        # Paddle 推理对象作为进程内单例复用，但底层引擎不假定线程安全。
        self._inference_lock = Lock()

    def _load_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRProviderError(
                "PaddleOCR is not installed; install the project's [ocr] extra"
            ) from exc
        try:
            # 关闭与文字识别无关的版面增强，降低启动时间和内存占用。
            self._engine = PaddleOCR(
                lang=self.lang,
                ocr_version=self.model_version,
                # Windows CPU 的 oneDNN 后端对部分 Paddle IR 属性仍不兼容；
                # Windows CPU 环境优先选择稳定的普通 Paddle 推理路径。
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        except TypeError:
            # 兼容仍使用旧版构造参数的本地 PaddleOCR 环境。
            try:
                self._engine = PaddleOCR(lang=self.lang)
            except Exception as exc:
                raise OCRProviderError("PaddleOCR could not be initialized") from exc
        except Exception as exc:
            raise OCRProviderError("PaddleOCR could not be initialized") from exc
        return self._engine

    def recognize(self, image: bytes, *, page_number: int) -> OCRPageResult:
        if not image:
            raise OCRProviderError("OCR image must not be empty")
        temporary_path: str | None = None
        try:
            with self._inference_lock:
                engine = self._load_engine()
                # 使用临时 PNG 兼容 PaddleOCR 2.x/3.x 的路径输入协议，识别后立即清理。
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporary:
                    temporary.write(image)
                    temporary_path = temporary.name
                if hasattr(engine, "predict"):
                    raw_result = engine.predict(temporary_path)
                elif hasattr(engine, "ocr"):
                    raw_result = engine.ocr(temporary_path, cls=False)
                else:
                    raise OCRProviderError("PaddleOCR engine exposes no supported inference method")
                # 3.x 可能返回惰性生成器，必须在锁和临时文件仍有效时完成归一化。
                return coerce_paddle_result(
                    raw_result,
                    page_number=page_number,
                    provider=self.provider_name,
                    model_version=self.model_version,
                )
        except OCRProviderError:
            raise
        except Exception as exc:
            raise OCRProviderError("PaddleOCR failed to recognize a PDF page") from exc
        finally:
            if temporary_path:
                try:
                    import os

                    os.unlink(temporary_path)
                except OSError:
                    logger.warning("Failed to remove temporary OCR image", exc_info=True)


def _json_like(value: Any) -> Any:
    """将 PaddleOCR 的结果对象/JSON 字符串展开为普通 Python 值。"""
    if hasattr(value, "json"):
        value = value.json() if callable(value.json) else value.json
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise OCRProviderError("PaddleOCR returned invalid JSON") from exc
    return value


def _as_sequence(value: Any) -> list[Any]:
    """把列表、NumPy 数组和 SDK 惰性序列统一为普通列表。"""
    if value is None:
        return []
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        value = tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return list(value)
    return []


def _unwrap_paddle_result(raw_result: Any) -> Any:
    value = _json_like(raw_result)
    if not isinstance(value, (str, bytes, Mapping)):
        value_list = _as_sequence(value)
        if value_list:
            value = value_list[0]
        elif value is not None:
            value = {}
        value = _json_like(value)
    if isinstance(value, Mapping) and isinstance(value.get("res"), Mapping):
        value = value["res"]
    return value


def _normalise_box(value: Any) -> tuple[tuple[float, float], ...] | None:
    points_value = _as_sequence(value)
    points: list[tuple[float, float]] = []
    for point in points_value:
        point_values = _as_sequence(point)
        if len(point_values) >= 2:
            try:
                points.append((float(point_values[0]), float(point_values[1])))
            except (TypeError, ValueError):
                return None
    if points:
        return tuple(points)
    return None


def _old_style_lines(value: Any) -> tuple[OCRTextLine, ...]:
    """兼容 PaddleOCR 2.x 的 ``[box, (text, score)]`` 列表。"""
    if not isinstance(value, list):
        return ()
    lines: list[OCRTextLine] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        payload = item[1]
        if not isinstance(payload, (list, tuple)) or len(payload) < 2:
            continue
        try:
            text = str(payload[0]).strip()
            score = float(payload[1])
            if text:
                lines.append(
                    OCRTextLine(
                        text=text,
                        confidence=max(0.0, min(1.0, score)),
                        bbox=_normalise_box(item[0]),
                    )
                )
        except (TypeError, ValueError):
            continue
    return tuple(lines)


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    """按兼容字段名取值，避免对 NumPy 数组执行不明确的布尔判断。"""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return []


def coerce_paddle_result(
    raw_result: Any,
    *,
    page_number: int,
    provider: str = "paddleocr",
    model_version: str = "PP-OCRv5",
) -> OCRPageResult:
    """把不同 PaddleOCR 版本的返回形态归一为项目契约。"""
    value = _unwrap_paddle_result(raw_result)
    if isinstance(value, list):
        return OCRPageResult(page_number, _old_style_lines(value), provider, model_version)
    if not isinstance(value, Mapping):
        raise OCRProviderError("PaddleOCR returned an unsupported result shape")

    texts = _first_present(value, "rec_texts", "texts", "text")
    scores = _first_present(value, "rec_scores", "scores")
    boxes = _first_present(value, "rec_boxes", "dt_polys", "boxes")
    if isinstance(texts, str):
        texts = [texts]
    else:
        texts = _as_sequence(texts)
    scores = _as_sequence(scores)
    boxes = _as_sequence(boxes)

    lines: list[OCRTextLine] = []
    for index, raw_text in enumerate(texts):
        text = str(raw_text).strip()
        if not text:
            continue
        try:
            score = float(scores[index]) if index < len(scores) else 0.0
        except (TypeError, ValueError):
            score = 0.0
        lines.append(
            OCRTextLine(
                text=text,
                confidence=max(0.0, min(1.0, score)),
                bbox=_normalise_box(boxes[index]) if index < len(boxes) else None,
            )
        )
    return OCRPageResult(page_number, tuple(lines), provider, model_version)


class OCRProcessingService:
    """把图片页识别结果合并为可追溯切片，并安全更新文档状态。"""

    def __init__(
        self,
        registry: ParserRegistry | None = None,
        chunker: DeterministicChunker | None = None,
        renderer: PageImageRenderer | None = None,
        provider: OCRProvider | None = None,
        min_confidence: float = 0.85,
    ) -> None:
        if not 0 <= min_confidence <= 1:
            raise ValueError("OCR minimum confidence must be between 0 and 1")
        self.registry = registry or create_default_parser_registry()
        self.chunker = chunker or DeterministicChunker()
        self.renderer = renderer or PypdfiumPageRenderer()
        self.provider = provider or PaddleOCRProvider()
        self.min_confidence = min_confidence

    async def process(
        self,
        document: Document,
        session: AsyncSession,
        storage: StorageService,
    ) -> Document:
        """只处理解析器标出的图片页；低置信度页保持待 OCR 状态。"""
        if not document.needs_ocr:
            raise OCRNotRequiredError("Document has no pending OCR pages")
        try:
            metadata = validate_upload_metadata(
                document.original_filename,
                document.content_type,
            )
        except DocumentValidationError as exc:
            raise OCRUnsupportedDocumentError("Document metadata is not valid") from exc
        if metadata.document_format is not DocumentFormat.PDF:
            raise OCRUnsupportedDocumentError("The OCR pilot currently supports scanned PDF only")

        document.status = "processing"
        document.ocr_status = "pending"
        document.error_message = None
        await session.flush()

        try:
            with storage.open(document.storage_key) as source:
                parsed = self.registry.parse(DocumentFormat.PDF, source)
            image_pages = tuple(parsed.image_only_page_numbers)
            if not image_pages:
                raise OCRNotRequiredError("Document has no pending OCR pages")
            with storage.open(document.storage_key) as source:
                rendered = await asyncio.to_thread(self.renderer.render_pages, source, image_pages)
        except DocumentParseError as exc:
            await DocumentProcessingService._replace_chunks(session, document, ())
            self._mark_failed(document, exc.safe_message, ParseWarning(exc.code, exc.safe_message))
            await session.commit()
            await session.refresh(document)
            return document

        warnings = list(parsed.warnings)
        ocr_sections: list[ParsedSection] = []
        unresolved_pages: list[int] = []
        provider_name = getattr(self.provider, "provider_name", "custom")
        model_version = getattr(self.provider, "model_version", "unknown")

        for page_number in image_pages:
            image = rendered.get(page_number)
            if not image:
                unresolved_pages.append(page_number)
                warnings.append(
                    ParseWarning("ocr_render_failed", "OCR image rendering failed", page_number)
                )
                continue
            result = await asyncio.to_thread(
                self.provider.recognize,
                image,
                page_number=page_number,
            )
            if result.page_number != page_number or not result.text:
                unresolved_pages.append(page_number)
                warnings.append(
                    ParseWarning("ocr_no_text", "OCR did not produce usable text", page_number)
                )
                continue
            if result.min_confidence < self.min_confidence:
                unresolved_pages.append(page_number)
                warnings.append(
                    ParseWarning(
                        "ocr_low_confidence",
                        f"OCR confidence is below {self.min_confidence:.2f}",
                        page_number,
                    )
                )
                continue
            warnings.append(ParseWarning("ocr_completed", "OCR text accepted", page_number))
            ocr_sections.append(
                ParsedSection(
                    order=0,
                    text=result.text,
                    page_number=page_number,
                    extraction_method="ocr",
                    source_metadata={
                        "ocr": {
                            "provider": result.provider,
                            "model_version": result.model_version,
                            "page_number": page_number,
                            "min_confidence": result.min_confidence,
                            "lines": [
                                {
                                    "text": line.text,
                                    "confidence": line.confidence,
                                    "bbox": [list(point) for point in line.bbox]
                                    if line.bbox is not None
                                    else None,
                                }
                                for line in result.lines
                            ],
                        }
                    },
                )
            )

        combined_sections = sorted(
            [*parsed.sections, *ocr_sections],
            key=lambda section: (section.page_number or 10**9, section.order),
        )
        sections = tuple(
            replace(section, order=order) for order, section in enumerate(combined_sections)
        )
        status = (
            TextExtractionStatus.PARTIAL
            if unresolved_pages or parsed.failed_page_numbers
            else TextExtractionStatus.COMPLETE
        )
        if not sections:
            status = TextExtractionStatus.NO_TEXT
        parsed_with_ocr = ParsedDocument(
            sections=sections,
            text_extraction_status=status,
            embedded_image_count=parsed.embedded_image_count,
            image_only_page_numbers=tuple(unresolved_pages),
            failed_page_numbers=parsed.failed_page_numbers,
            warnings=tuple(warnings),
            page_count=parsed.page_count,
            requires_ocr=bool(unresolved_pages),
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
        )
        drafts = self.chunker.chunk(parsed_with_ocr)
        chunks = DocumentProcessingService._build_chunks(document, drafts)
        await DocumentProcessingService._replace_chunks(session, document, chunks)

        now = datetime.now(UTC)
        document.status = "ready" if chunks else "failed"
        document.error_message = None if chunks else "OCR did not produce reliable text"
        document.image_only_page_count = len(unresolved_pages)
        document.text_extraction_status = status
        document.needs_ocr = bool(unresolved_pages)
        document.parse_warnings = [warning.as_dict() for warning in warnings]
        document.parsed_at = now
        document.ocr_status = (
            "completed" if not unresolved_pages else ("partial" if chunks else "failed")
        )
        document.ocr_provider = provider_name
        document.ocr_model_version = model_version
        document.ocr_processed_at = now
        document.ocr_low_confidence_page_count = sum(
            warning.code == "ocr_low_confidence" for warning in warnings
        )
        await session.commit()
        await session.refresh(document)
        return document

    @staticmethod
    def _mark_failed(document: Document, message: str, warning: ParseWarning) -> None:
        document.status = "failed"
        document.error_message = message
        document.text_extraction_status = TextExtractionStatus.FAILED
        document.needs_ocr = True
        document.ocr_status = "failed"
        document.parse_warnings = [warning.as_dict()]
        document.ocr_processed_at = datetime.now(UTC)


@lru_cache(maxsize=1)
def get_ocr_processing_service() -> OCRProcessingService:
    """FastAPI 默认的 OCR 编排服务；PaddleOCR 仍保持延迟加载。"""
    settings = get_settings()
    return OCRProcessingService(
        renderer=PypdfiumPageRenderer(scale=settings.ocr_pdf_render_scale),
        provider=PaddleOCRProvider(lang=settings.ocr_language),
        min_confidence=settings.ocr_min_confidence,
    )
