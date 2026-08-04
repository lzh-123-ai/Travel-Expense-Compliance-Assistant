import pytest

from app.services.parsing import ParsedDocument, ParsedSection, ParseWarning, TextExtractionStatus


def test_parsed_document_keeps_real_page_provenance_and_warnings() -> None:
    parsed = ParsedDocument(
        sections=(
            ParsedSection(order=0, text="住宿费标准", page_number=1),
            ParsedSection(order=1, text="交通费标准", page_number=3),
        ),
        text_extraction_status=TextExtractionStatus.PARTIAL,
        embedded_image_count=1,
        image_only_page_numbers=(2,),
        warnings=(
            ParseWarning(
                code="image_only_page",
                message="Page contains an image but no extractable text",
                page_number=2,
            ),
        ),
        page_count=3,
        parser_name="fixture",
    )

    assert parsed.needs_ocr is True
    assert parsed.image_only_page_count == 1
    assert parsed.character_count == 10
    assert parsed.serialized_warnings()[0]["page_number"] == 2


def test_docx_section_uses_heading_path_without_fake_page_number() -> None:
    section = ParsedSection(
        order=0,
        text="一线城市住宿上限为 600 元。",
        heading_path=("差旅费管理办法", "住宿费"),
    )

    assert section.page_number is None
    assert section.heading_path == ("差旅费管理办法", "住宿费")


def test_partial_extraction_requires_unextracted_page() -> None:
    with pytest.raises(ValueError, match="unextracted page"):
        ParsedDocument(
            sections=(ParsedSection(order=0, text="制度正文", page_number=1),),
            text_extraction_status=TextExtractionStatus.PARTIAL,
            page_count=1,
            parser_name="fixture",
        )


def test_no_text_result_cannot_hide_extracted_sections() -> None:
    with pytest.raises(ValueError, match="must not contain sections"):
        ParsedDocument(
            sections=(ParsedSection(order=0, text="unexpected"),),
            text_extraction_status=TextExtractionStatus.NO_TEXT,
            parser_name="fixture",
        )
