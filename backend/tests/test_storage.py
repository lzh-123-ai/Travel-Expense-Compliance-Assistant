import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from app.services.storage import DocumentTooLargeError, LocalStorage


class AsyncBytesReader:
    def __init__(self, content: bytes) -> None:
        self.stream = BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)


@pytest.mark.asyncio
async def test_local_storage_saves_atomically_and_calculates_hash(tmp_path: Path) -> None:
    content = "国内差旅住宿标准".encode()
    storage = LocalStorage(tmp_path)

    stored = await storage.save(AsyncBytesReader(content), "documents/policy.txt", 1024)

    assert stored.size == len(content)
    assert stored.sha256 == hashlib.sha256(content).hexdigest()
    assert (tmp_path / stored.key).read_bytes() == content
    assert list((tmp_path / ".incoming").glob("*")) == []


@pytest.mark.asyncio
async def test_local_storage_removes_partial_file_when_size_limit_is_exceeded(
    tmp_path: Path,
) -> None:
    storage = LocalStorage(tmp_path)

    with pytest.raises(DocumentTooLargeError):
        await storage.save(AsyncBytesReader(b"too large"), "documents/policy.txt", 3)

    assert not (tmp_path / "documents/policy.txt").exists()
    assert list((tmp_path / ".incoming").glob("*")) == []


def test_local_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        storage.open("../outside.txt")


def test_local_storage_can_quarantine_restore_and_purge(tmp_path: Path) -> None:
    source = tmp_path / "documents/policy.txt"
    source.parent.mkdir(parents=True)
    source.write_text("policy", encoding="utf-8")
    storage = LocalStorage(tmp_path)

    first = storage.quarantine("documents/policy.txt")
    assert not source.exists()
    storage.restore(first)
    assert source.read_text(encoding="utf-8") == "policy"

    second = storage.quarantine("documents/policy.txt")
    storage.purge(second)
    assert not source.exists()
    assert not (tmp_path / (second.quarantine_key or "missing")).exists()
