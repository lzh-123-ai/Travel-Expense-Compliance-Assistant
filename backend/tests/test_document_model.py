from uuid import UUID

from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase


def test_document_model_has_expected_table_and_columns() -> None:
    assert Document.__tablename__ == "documents"
    assert set(Document.__table__.columns.keys()) == {
        "id",
        "knowledge_base_id",
        "original_filename",
        "content_type",
        "file_size",
        "storage_key",
        "sha256",
        "status",
        "error_message",
        "policy_type",
        "version_label",
        "effective_from",
        "effective_to",
        "access_scope",
        "supersedes_document_id",
        "embedded_image_count",
        "image_only_page_count",
        "text_extraction_status",
        "needs_ocr",
        "parse_warnings",
        "parsed_at",
        "created_at",
        "updated_at",
    }
    assert Document.__table__.c.id.type.python_type is UUID
    assert Document.__table__.c.storage_key.unique is True

    duplicate_index = next(
        index
        for index in Document.__table__.indexes
        if index.name == "uq_documents_knowledge_base_sha256"
    )
    assert duplicate_index.unique is True
    assert [column.name for column in duplicate_index.columns] == [
        "knowledge_base_id",
        "sha256",
    ]


def test_document_belongs_to_a_knowledge_base() -> None:
    foreign_key = next(iter(Document.__table__.c.knowledge_base_id.foreign_keys))

    assert foreign_key.target_fullname == "knowledge_bases.id"
    assert foreign_key.ondelete == "CASCADE"
    assert Document.__table__.c.knowledge_base_id.index is True
    assert Document.knowledge_base.property.mapper.class_ is KnowledgeBase
    assert KnowledgeBase.documents.property.mapper.class_ is Document


def test_document_status_has_application_and_database_defaults() -> None:
    status_column = Document.__table__.c.status

    assert status_column.default is not None
    assert status_column.default.arg == "pending"
    assert status_column.server_default is not None
    assert status_column.server_default.arg == "pending"

    extraction_column = Document.__table__.c.text_extraction_status
    assert extraction_column.default is not None
    assert extraction_column.default.arg == "not_attempted"
    assert extraction_column.server_default is not None
    assert extraction_column.server_default.arg == "not_attempted"
