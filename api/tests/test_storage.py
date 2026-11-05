"""对象存储：目录穿越防护。

曾经的实现用 `str(target).startswith(str(root))` 做判断，
`storage-evil/` 与 `storage/` 共享字符串前缀，可以直接写到根目录外。
接上传接口之前必须守住这条。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from infra.storage.base import StorageError
from infra.storage.local import LocalStorage


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    # 注意：根目录故意叫 storage，才能复现 "storage-evil" 同前缀绕过
    return LocalStorage(root=str(tmp_path / "storage"))


@pytest.mark.parametrize(
    "bad_key",
    [
        "../storage-evil/pwn.txt",
        "../../etc/passwd",
        "a/../../b.txt",
        "./hidden.txt",
        "C:/Windows/win.ini",
        "..\\windows\\x.txt",
        "",
        "   ",
        "/",
    ],
)
def test_rejects_traversal_keys(storage: LocalStorage, bad_key: str) -> None:
    with pytest.raises(StorageError):
        asyncio.run(storage.put(bad_key, b"1"))


def test_rejects_write_outside_root(storage: LocalStorage, tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        asyncio.run(storage.put("../storage-evil/pwn.txt", b"1"))
    assert not (tmp_path / "storage-evil").exists()


def test_accepts_nested_keys(storage: LocalStorage) -> None:
    url = asyncio.run(storage.put("assets/2026/11/cover.webp", b"data"))
    assert url.endswith("/assets/2026/11/cover.webp")
    assert asyncio.run(storage.get("assets/2026/11/cover.webp")) == b"data"
    assert (storage.root / "assets/2026/11/cover.webp").is_file()


def test_presigned_upload_url_fails_loudly(storage: LocalStorage) -> None:
    """没有直传端点时不要返回 404 死链接。"""
    with pytest.raises(StorageError):
        asyncio.run(storage.presigned_upload_url("assets/a.png"))
