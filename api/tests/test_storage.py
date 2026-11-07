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


def test_delete_is_idempotent_even_if_the_file_vanishes_midway(
    storage: LocalStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """协议写的是「不存在时幂等返回」，那就不能有「检查完再删」的窗口。

    旧实现是 `if path.exists(): path.unlink()`：两次删除并发（重试、两个实例的
    清理任务、用户连点）时，第二个会读到 True 然后删一个已经不存在的文件，
    `FileNotFoundError` 不在 `StorageError` 里，于是 500。用户看到的是
    「删除素材偶发失败」，刷新一次又好了。
    """
    target = storage.root / "assets/gone.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")

    # exists 说「在」，但真正删的时候已经被别人删掉了——竞态的最小复现
    monkeypatch.setattr(Path, "exists", lambda self: True)
    target.unlink()

    asyncio.run(storage.delete("assets/gone.bin"))  # 不该抛
    asyncio.run(storage.delete("assets/gone.bin"))  # 真的不存在也不该抛
