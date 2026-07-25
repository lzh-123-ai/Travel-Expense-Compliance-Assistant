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
        "storage_path",
        "status",
        "error_message",
        "created_at",
        "updated_at",
    }
    assert Document.__table__.c.id.type.python_type is UUID
    assert Document.__table__.c.storage_path.unique is True


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
