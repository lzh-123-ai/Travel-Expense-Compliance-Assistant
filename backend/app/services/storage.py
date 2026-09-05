"""带安全路径和删除补偿能力的文件系统适配器。

数据库无法用事务控制磁盘。上传和删除路由因此通过本协议，在数据库提交前后
显式执行清理、隔离、恢复或彻底删除。
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class DocumentTooLargeError(Exception):
    def __init__(self, max_size: int) -> None:
        super().__init__(f"Document exceeds the {max_size} byte size limit")
        self.max_size = max_size


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str


@dataclass(frozen=True)
class QuarantinedObject:
    original_key: str
    quarantine_key: str | None

    @property
    def existed(self) -> bool:
        return self.quarantine_key is not None


class StorageService(Protocol):
    """文档路由依赖的可替换存储契约。"""
    async def save(self, source: AsyncReadable, key: str, max_size: int) -> StoredObject: ...

    def open(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...

    def quarantine(self, key: str) -> QuarantinedObject: ...

    def restore(self, item: QuarantinedObject) -> None: ...

    def purge(self, item: QuarantinedObject) -> None: ...


class LocalStorage:
    """以相对 key 访问本地文件，并阻止路径逃逸出存储根目录。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _resolve(self, key: str) -> Path:
        """解析相对 key，同时阻止路径逃逸出存储根目录。"""
        candidate = (self.root / key).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Storage key escapes the configured root") from exc
        return candidate

    async def save(self, source: AsyncReadable, key: str, max_size: int) -> StoredObject:
        """边流式计算哈希边写入临时文件，完成后原子发布。"""
        destination = self._resolve(key)
        incoming_dir = self._resolve(".incoming")
        incoming_dir.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = incoming_dir / f"{uuid4()}.tmp"
        size = 0
        digest = hashlib.sha256()

        try:
            with temporary.open("xb") as output:
                while chunk := await source.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_size:
                        raise DocumentTooLargeError(max_size)
                    digest.update(chunk)
                    output.write(chunk)
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return StoredObject(key=key, size=size, sha256=digest.hexdigest())

    def open(self, key: str) -> BinaryIO:
        return self._resolve(key).open("rb")

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)

    def quarantine(self, key: str) -> QuarantinedObject:
        """先隐藏文件，使数据库删除失败时仍可恢复。"""
        source = self._resolve(key)
        if not source.exists():
            return QuarantinedObject(original_key=key, quarantine_key=None)

        quarantine_key = f".trash/{uuid4()}"
        destination = self._resolve(quarantine_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        return QuarantinedObject(original_key=key, quarantine_key=quarantine_key)

    def restore(self, item: QuarantinedObject) -> None:
        if not item.existed:
            return
        source = self._resolve(item.quarantine_key or "")
        destination = self._resolve(item.original_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            os.replace(source, destination)

    def purge(self, item: QuarantinedObject) -> None:
        if item.existed:
            self._resolve(item.quarantine_key or "").unlink(missing_ok=True)


def get_storage_service() -> LocalStorage:
    """根据配置的上传根目录构造本地存储适配器。"""
    return LocalStorage(get_settings().upload_dir)


def purge_after_commit(storage: StorageService, item: QuarantinedObject) -> None:
    """提交已成功时，隔离区清理失败只记录日志，避免向客户端谎报删除失败。"""
    try:
        storage.purge(item)
    except OSError:
        logger.exception(
            "Failed to purge quarantined object",
            extra={"storage_key": item.original_key},
        )
