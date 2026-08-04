from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.schemas.document import (
    DocumentChunkResponse,
    DocumentMetadataUpdate,
    DocumentResponse,
)
from app.services.document_processing import (
    DocumentProcessingService,
    get_document_processing_service,
)
from app.services.document_validation import (
    DocumentValidationError,
    InvalidFilenameError,
    UnsupportedDocumentTypeError,
    validate_stored_content,
    validate_upload_metadata,
)
from app.services.storage import (
    DocumentTooLargeError,
    StorageService,
    get_storage_service,
    purge_after_commit,
)

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/documents")
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
DocumentStorage = Annotated[StorageService, Depends(get_storage_service)]
DocumentProcessor = Annotated[DocumentProcessingService, Depends(get_document_processing_service)]


async def _require_knowledge_base(session: AsyncSession, knowledge_base_id: UUID) -> None:
    if await session.get(KnowledgeBase, knowledge_base_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )


async def _get_document(
    session: AsyncSession,
    knowledge_base_id: UUID,
    document_id: UUID,
) -> Document:
    statement = select(Document).where(
        Document.id == document_id,
        Document.knowledge_base_id == knowledge_base_id,
    )
    result = await session.execute(statement)
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return document


async def _get_document_for_processing(
    session: AsyncSession,
    knowledge_base_id: UUID,
    document_id: UUID,
) -> Document:
    """锁定同一文档的处理事务，避免并发重建相同 ordinal 的分块。"""
    statement = (
        select(Document)
        .where(
            Document.id == document_id,
            Document.knowledge_base_id == knowledge_base_id,
        )
        .with_for_update()
    )
    result = await session.execute(statement)
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return document


async def _find_duplicate_id(
    session: AsyncSession,
    knowledge_base_id: UUID,
    sha256: str,
) -> UUID | None:
    result = await session.execute(
        select(Document.id).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.sha256 == sha256,
        )
    )
    return result.scalar_one_or_none()


def _duplicate_document_error(document_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "Document content already exists",
            "existing_document_id": str(document_id),
        },
    )


@router.get("", response_model=list[DocumentResponse], summary="List documents")
async def list_documents(
    knowledge_base_id: UUID,
    session: DatabaseSession,
) -> list[Document]:
    await _require_knowledge_base(session, knowledge_base_id)
    statement = (
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base_id)
        .order_by(Document.created_at.desc())
    )
    result = await session.execute(statement)
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentResponse, summary="Get a document")
async def get_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
) -> Document:
    return await _get_document(session, knowledge_base_id, document_id)


@router.get(
    "/{document_id}/chunks",
    response_model=list[DocumentChunkResponse],
    summary="List traceable document chunks",
)
async def list_document_chunks(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
) -> list[DocumentChunk]:
    await _get_document(session, knowledge_base_id, document_id)
    result = await session.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.ordinal)
    )
    return list(result.scalars().all())


@router.patch(
    "/{document_id}/metadata",
    response_model=DocumentResponse,
    summary="Update document policy metadata",
)
async def update_document_metadata(
    knowledge_base_id: UUID,
    document_id: UUID,
    payload: DocumentMetadataUpdate,
    session: DatabaseSession,
) -> Document:
    document = await _get_document(session, knowledge_base_id, document_id)
    update_fields = payload.model_dump(exclude_unset=True)
    effective_from = update_fields.get("effective_from", document.effective_from)
    effective_to = update_fields.get("effective_to", document.effective_to)
    if effective_from is not None and effective_to is not None and effective_to < effective_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="effective_to must not be earlier than effective_from",
        )
    if payload.supersedes_document_id == document.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A document cannot supersede itself",
        )
    if payload.supersedes_document_id is not None:
        superseded = await _get_document(session, knowledge_base_id, payload.supersedes_document_id)
        if superseded.id == document.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A document cannot supersede itself",
            )

    for field, value in update_fields.items():
        setattr(document, field, value)
    await session.commit()
    await session.refresh(document)
    return document


@router.post(
    "/{document_id}/process",
    response_model=DocumentResponse,
    summary="Parse and deterministically chunk a document",
)
async def process_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
    storage: DocumentStorage,
    processor: DocumentProcessor,
) -> Document:
    document = await _get_document_for_processing(session, knowledge_base_id, document_id)
    return await processor.process(document, session, storage)


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
)
async def upload_document(
    knowledge_base_id: UUID,
    file: Annotated[UploadFile, File(description="PDF, DOCX, Markdown, or UTF-8 text")],
    session: DatabaseSession,
    storage: DocumentStorage,
) -> Document:
    storage_key: str | None = None
    try:
        await _require_knowledge_base(session, knowledge_base_id)
        try:
            metadata = validate_upload_metadata(file.filename, file.content_type)
        except InvalidFilenameError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        except UnsupportedDocumentTypeError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=str(exc),
            ) from exc

        settings = get_settings()
        document_id = uuid4()
        storage_key = f"documents/{document_id}{metadata.extension}"
        try:
            stored = await storage.save(
                file,
                storage_key,
                settings.max_document_size_bytes,
            )
            validate_stored_content(
                storage,
                storage_key,
                metadata.document_format,
                settings.max_docx_uncompressed_size_bytes,
            )
        except DocumentTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=str(exc),
            ) from exc
        except DocumentValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

        existing_id = await _find_duplicate_id(session, knowledge_base_id, stored.sha256)
        if existing_id is not None:
            raise _duplicate_document_error(existing_id)

        document = Document(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            original_filename=metadata.filename,
            content_type=metadata.content_type,
            file_size=stored.size,
            storage_key=stored.key,
            sha256=stored.sha256,
        )
        session.add(document)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            storage.delete(storage_key)
            storage_key = None
            existing_id = await _find_duplicate_id(session, knowledge_base_id, stored.sha256)
            if existing_id is None:
                raise
            raise _duplicate_document_error(existing_id) from exc

        await session.refresh(document)
        return document
    except Exception:
        if storage_key is not None:
            storage.delete(storage_key)
        if session.in_transaction():
            await session.rollback()
        raise
    finally:
        await file.close()


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
)
async def delete_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
    storage: DocumentStorage,
) -> Response:
    document = await _get_document(session, knowledge_base_id, document_id)
    try:
        quarantined = storage.quarantine(document.storage_key)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document storage is temporarily unavailable",
        ) from exc

    try:
        await session.delete(document)
        await session.commit()
    except Exception:
        await session.rollback()
        try:
            storage.restore(quarantined)
        except OSError as restore_exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document deletion failed and storage recovery requires attention",
            ) from restore_exc
        raise

    purge_after_commit(storage, quarantined)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
