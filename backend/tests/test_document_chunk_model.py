from app.models.document import Document
from app.models.document_chunk import DocumentChunk


def test_document_chunk_has_traceable_content_columns() -> None:
    assert DocumentChunk.__tablename__ == "document_chunks"
    assert set(DocumentChunk.__table__.columns.keys()) == {
        "id",
        "document_id",
        "ordinal",
        "content",
        "page_start",
        "page_end",
        "section_path",
        "char_count",
        "token_estimate",
        "content_hash",
        "extraction_method",
        "chunking_strategy",
        "chunk_size",
        "chunk_overlap",
        "created_at",
    }

    unique_order = next(
        index
        for index in DocumentChunk.__table__.indexes
        if index.name == "uq_document_chunks_document_ordinal"
    )
    assert unique_order.unique is True
    assert [column.name for column in unique_order.columns] == ["document_id", "ordinal"]


def test_document_chunk_is_deleted_with_its_document() -> None:
    foreign_key = next(iter(DocumentChunk.__table__.c.document_id.foreign_keys))

    assert foreign_key.target_fullname == "documents.id"
    assert foreign_key.ondelete == "CASCADE"
    assert DocumentChunk.document.property.mapper.class_ is Document
    assert Document.chunks.property.mapper.class_ is DocumentChunk
