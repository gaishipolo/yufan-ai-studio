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


def test_put_file_keeps_bytes_when_source_is_the_target(
    storage: LocalStorage, tmp_path: Path
) -> None:
    """源和目标同一个文件时不能拷（拷＝先截断目标再读源，读到 0 字节）。

    入库把 `storageKey` 当 source 传回来时正是这种情形：本地驱动直接返回
    `local_path(key)`，而 key 已经是内容寻址地址。修这条之前实测 1000 → 0。
    """
    payload = b"x" * 1000
    asyncio.run(storage.put("blobs/aa/hash.mp4", payload))
    target = storage._resolve("blobs/aa/hash.mp4")

    digest = asyncio.run(storage.put_file("blobs/aa/hash.mp4", target))

    assert target.read_bytes() == payload
    assert len(digest) == 64


def test_put_file_leaves_no_part_files(storage: LocalStorage, tmp_path: Path) -> None:
    """临时文件必须落成正式对象 —— 残留的 `*.part` 会随 GC 一起漂在盘上。"""
    source = tmp_path / "src.bin"
    source.write_bytes(b"y" * 512)

    asyncio.run(storage.put_file("blobs/bb/other.mp4", source))

    root = storage._resolve("blobs/bb")
    assert sorted(item.name for item in root.iterdir()) == ["other.mp4"]


def test_put_replaces_existing_object_without_leaving_partials(
    storage: LocalStorage, tmp_path: Path
) -> None:
    asyncio.run(storage.put("blobs/cc/obj.bin", b"old"))
    asyncio.run(storage.put("blobs/cc/obj.bin", b"new-content"))

    target = storage._resolve("blobs/cc/obj.bin")
    assert target.read_bytes() == b"new-content"
    assert [item.name for item in target.parent.iterdir()] == ["obj.bin"]


def test_concurrent_put_file_to_the_same_key_never_interleaves(
    storage: LocalStorage, tmp_path: Path
) -> None:
    """两个协程同时写同一个 key：结果必须是**完整的其中一份**，不能互相截断。

    以前的实现对目标 `open("wb")`，两个写者各自截断对方，长度会是两份的任意
    混合；现在各写各的 uuid4 临时文件，最后 `replace`，谁后到谁赢。
    """
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"a" * 4096)
    second.write_bytes(b"b" * 8192)

    async def race() -> None:
        await asyncio.gather(
            storage.put_file("blobs/dd/race.bin", first),
            storage.put_file("blobs/dd/race.bin", second),
        )

    asyncio.run(race())

    data = storage._resolve("blobs/dd/race.bin").read_bytes()
    assert data in {b"a" * 4096, b"b" * 8192}


