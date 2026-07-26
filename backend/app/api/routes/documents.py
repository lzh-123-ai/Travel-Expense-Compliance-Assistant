from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.schemas.document import DocumentResponse

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/documents")
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


async def _write_upload(upload: UploadFile, destination: Path, max_size: int) -> int:
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"Document exceeds the {max_size} byte size limit",
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return size


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    knowledge_base_id: UUID,
    file: Annotated[UploadFile, File(description="Document to upload")],
    session: DatabaseSession,
) -> Document:
    knowledge_base = await session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )

    original_filename = file.filename or "uploaded-document"
    if len(original_filename) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filename must be at most 255 characters",
        )

    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    document_id = uuid4()
    suffix = Path(original_filename).suffix[:20]
    destination = settings.upload_dir / f"{document_id}{suffix}"

    try:
        file_size = await _write_upload(file, destination, settings.max_document_size_bytes)
        document = Document(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            original_filename=original_filename,
            content_type=file.content_type or "application/octet-stream",
            file_size=file_size,
            storage_path=str(destination),
        )
        session.add(document)
        await session.commit()
        await session.refresh(document)
    except Exception:
        await session.rollback()
        destination.unlink(missing_ok=True)
        raise

    return document
