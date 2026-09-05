"""文档上传、处理、索引和删除的 HTTP 接口及跨存储补偿。"""

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
from app.schemas.retrieval import DocumentEmbeddingResponse, DocumentKeywordIndexResponse
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
from app.services.embeddings import (
    DocumentEmbeddingService,
    DocumentNotReadyForEmbeddingError,
    EmbeddingProvider,
    EmbeddingProviderError,
    get_document_embedding_service,
    get_embedding_provider,
)
from app.services.keyword_indexing import (
    DocumentKeywordIndexingService,
    DocumentNotReadyForKeywordIndexingError,
    get_document_keyword_indexing_service,
)
from app.services.ocr import (
    OCRNotRequiredError,
    OCRProcessingError,
    OCRProcessingService,
    OCRProviderError,
    OCRUnsupportedDocumentError,
    get_ocr_processing_service,
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
DocumentOCRProcessor = Annotated[OCRProcessingService, Depends(get_ocr_processing_service)]
EmbeddingProviderDependency = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
DocumentEmbedder = Annotated[DocumentEmbeddingService, Depends(get_document_embedding_service)]
DocumentKeywordIndexer = Annotated[
    DocumentKeywordIndexingService,
    Depends(get_document_keyword_indexing_service),
]


async def _require_knowledge_base(session: AsyncSession, knowledge_base_id: UUID) -> None:
    """提前失败，避免文档操作指向不存在的知识库。"""
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
    """仅在文档属于 URL 中知识库时才加载它。

    两个查询条件构成 IDOR 越权边界：只知道文档 UUID，不能通过另一个
    知识库 URL 访问它。
    """
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
    """应用层快速查重；最终并发保障仍是数据库唯一约束。"""
    result = await session.execute(
        select(Document.id).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.sha256 == sha256,
        )
    )
    return result.scalar_one_or_none()


def _duplicate_document_error(document_id: UUID) -> HTTPException:
    """返回安全的冲突响应，不泄露已有文档内容。"""
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
    """只列出当前知识库中的文档。"""
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
    """完成知识库归属校验后返回单个文档。"""
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
    """按确定性的文档顺序返回可追溯切片，供排查和查看。"""
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
    """更新制度元数据；检索时会将其作为数据库过滤条件执行。"""
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
    """获取单文档行锁后执行解析，防止并发重建切片。"""
    document = await _get_document_for_processing(session, knowledge_base_id, document_id)
    return await processor.process(document, session, storage)


@router.post(
    "/{document_id}/ocr",
    response_model=DocumentResponse,
    summary="Run controlled OCR for scanned PDF pages",
)
async def ocr_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
    storage: DocumentStorage,
    processor: DocumentOCRProcessor,
) -> Document:
    """显式处理扫描版 PDF 图片页，原生解析链路不会自动触发 OCR。"""
    document = await _get_document_for_processing(session, knowledge_base_id, document_id)
    try:
        return await processor.process(document, session, storage)
    except OCRNotRequiredError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except OCRUnsupportedDocumentError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except OCRProviderError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except OCRProcessingError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OCR processing failed",
        ) from exc


@router.post(
    "/{document_id}/embeddings",
    response_model=DocumentEmbeddingResponse,
    summary="Create or refresh document chunk embeddings",
)
async def embed_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
    provider: EmbeddingProviderDependency,
    embedder: DocumentEmbedder,
) -> DocumentEmbeddingResponse:
    """仅创建缺失或过期的向量；Provider 失败时安全回滚。"""
    document = await _get_document(session, knowledge_base_id, document_id)
    try:
        result = await embedder.index_document(document, session, provider)
    except DocumentNotReadyForEmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except EmbeddingProviderError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return DocumentEmbeddingResponse(
        document_id=result.document_id,
        provider_name=result.provider_name,
        model_name=result.model_name,
        dimension=result.dimension,
        total_chunks=result.total_chunks,
        embedded_chunks=result.embedded_chunks,
        skipped_chunks=result.skipped_chunks,
    )


@router.post(
    "/{document_id}/keyword-index",
    response_model=DocumentKeywordIndexResponse,
    summary="Create or refresh the document keyword index",
)
async def index_document_keywords(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
    indexer: DocumentKeywordIndexer,
) -> DocumentKeywordIndexResponse:
    """仅为 ready 文档创建缺失或过期的关键词字段。"""
    document = await _get_document(session, knowledge_base_id, document_id)
    try:
        result = await indexer.index_document(document, session)
    except DocumentNotReadyForKeywordIndexingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return DocumentKeywordIndexResponse(
        document_id=result.document_id,
        tokenizer=result.tokenizer,
        total_chunks=result.total_chunks,
        indexed_chunks=result.indexed_chunks,
        skipped_chunks=result.skipped_chunks,
    )


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
    """持久化已校验的上传，并同时使用应用层和数据库层去重。

    只有落盘后才能计算哈希。若并发请求撞上唯一索引，本请求写入的物理文件
    必须删除；外层清理同时覆盖校验失败和意外数据库异常。
    """
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

        # 此处只为快速返回用户提示；下方 ``commit`` 才是并发安全的最终去重。
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
            # 快速查询后，另一请求已提交相同知识库/哈希；只删除本请求的物理副本。
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
        # 文件系统和数据库不能共享事务，因此在这里执行补偿清理。
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
    """用隔离/恢复模拟跨文件系统和数据库的删除事务。

    文件先从正式目录隐藏；若数据库删除不能提交则恢复。只有数据库成为最终
    事实后，才真正清理隔离区文件。
    """
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
        # 数据库侧未持久化时，撤销文件系统这一半操作。
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
