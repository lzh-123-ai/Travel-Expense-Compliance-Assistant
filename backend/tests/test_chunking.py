from app.services.chunking import ChunkingConfig, DeterministicChunker
from app.services.parsing import ParsedDocument, ParsedSection, TextExtractionStatus


def test_chunker_keeps_contiguous_pdf_page_range() -> None:
    parsed = ParsedDocument(
        sections=(
            ParsedSection(order=0, text="第一页制度正文", page_number=1),
            ParsedSection(order=1, text="第二页制度正文", page_number=2),
        ),
        text_extraction_status=TextExtractionStatus.COMPLETE,
        page_count=2,
        parser_name="fixture",
    )

    chunks = DeterministicChunker().chunk(parsed)

    assert len(chunks) == 1
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2
    assert chunks[0].content == "第一页制度正文\n\n第二页制度正文"


def test_chunker_does_not_bridge_unextracted_pdf_page() -> None:
    parsed = ParsedDocument(
        sections=(
            ParsedSection(order=0, text="第一页正文", page_number=1),
            ParsedSection(order=1, text="第三页正文", page_number=3),
        ),
        text_extraction_status=TextExtractionStatus.PARTIAL,
        image_only_page_numbers=(2,),
        page_count=3,
        parser_name="fixture",
    )

    chunks = DeterministicChunker().chunk(parsed)

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 1), (3, 3)]
    assert all("第二页" not in chunk.content for chunk in chunks)


def test_chunker_separates_docx_heading_paths() -> None:
    parsed = ParsedDocument(
        sections=(
            ParsedSection(order=0, text="住宿标准", heading_path=("制度", "住宿")),
            ParsedSection(order=1, text="交通标准", heading_path=("制度", "交通")),
        ),
        text_extraction_status=TextExtractionStatus.COMPLETE,
        parser_name="fixture",
    )

    chunks = DeterministicChunker().chunk(parsed)

    assert [chunk.section_path for chunk in chunks] == [
        ("制度", "住宿"),
        ("制度", "交通"),
    ]
    assert all(chunk.page_start is None for chunk in chunks)


def test_long_section_is_deterministic_and_overlapping() -> None:
    text = "住宿费标准。" * 80
    parsed = ParsedDocument(
        sections=(ParsedSection(order=0, text=text, page_number=4),),
        text_extraction_status=TextExtractionStatus.COMPLETE,
        page_count=4,
        parser_name="fixture",
    )
    chunker = DeterministicChunker(ChunkingConfig(max_characters=120, overlap_characters=20))

    first = chunker.chunk(parsed)
    second = chunker.chunk(parsed)

    assert first == second
    assert len(first) > 1
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))
    assert all(chunk.page_start == chunk.page_end == 4 for chunk in first)
    assert all(chunk.char_count <= 120 for chunk in first)
    assert all(len(chunk.content_hash) == 64 for chunk in first)
    assert all(chunk.extraction_method == "native_text" for chunk in first)
    assert first[0].content[-10:].strip() in first[1].content


def test_no_text_document_creates_no_chunks() -> None:
    parsed = ParsedDocument(
        sections=(),
        text_extraction_status=TextExtractionStatus.NO_TEXT,
        parser_name="fixture",
    )

    assert DeterministicChunker().chunk(parsed) == ()
