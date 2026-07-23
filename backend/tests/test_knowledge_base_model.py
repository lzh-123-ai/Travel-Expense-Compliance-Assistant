from uuid import UUID

from app.models.knowledge_base import KnowledgeBase


def test_knowledge_base_model_has_expected_table_and_columns() -> None:
    assert KnowledgeBase.__tablename__ == "knowledge_bases"
    assert set(KnowledgeBase.__table__.columns.keys()) == {
        "id",
        "name",
        "description",
        "created_at",
        "updated_at",
    }
    assert KnowledgeBase.__table__.c.name.unique is True


def test_knowledge_base_primary_key_is_a_postgresql_uuid() -> None:
    assert KnowledgeBase.__table__.c.id.type.python_type is UUID
