"""取件的第三种交法：同源流式转发（`Served.stream`）。

为什么值得单独测：`307` 与「流式转发」在纸面上都是「把字节交给浏览器」，
但前者要求**目标域**回 CORS 头——桶的 CORS 是环境配置，换部署就丢；后者同源，
不依赖任何配置。资产库的「下载 zip」与「下载原件」都走 XHR 取 blob，所以必须后者；
而缩略图是 `<img>` 直接引用，`307` 交给对象存储更省带宽，不该被顺手改掉。

这条判断写错时，界面看起来仍然是好的（能下载、图能显示），只有控制台在报
「blocked by CORS policy」——正是那种「不写成用例就没人会发现」的错。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from core.serving import Served
from infra.storage.local import LocalStorage
from modules.library.service import LibraryService


class _RemoteStorage:
    """只回答「我没有本地路径，但有字节流」，模拟 S3 驱动。"""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def local_path(self, key: str) -> Path | None:
        return None

    def iter_chunks(
        self, key: str, *, chunk_size: int = 1024 * 1024
    ) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            for start in range(0, len(self.payload), chunk_size):
                yield self.payload[start : start + chunk_size]

        return _gen()

    async def presigned_download_url(self, key: str, *, expires_in: int = 900) -> str:
        return f"https://bucket.example/{key}?sig=stub"


def test_local_storage_streams_in_chunks(tmp_path: Path) -> None:
    """分块读：`chunk_size` 要真的生效，否则「流式」等于整块读进内存。"""
    storage = LocalStorage(root=str(tmp_path), public_base="/static")
    (tmp_path / "a.bin").write_bytes(b"0123456789")

    async def collect() -> list[bytes]:
        return [chunk async for chunk in storage.iter_chunks("a.bin", chunk_size=4)]

    assert asyncio.run(collect()) == [b"0123", b"4567", b"89"]


def test_served_stream_sets_length_and_filename() -> None:
    """`Content-Length` 决定浏览器能不能显示进度；文件名决定用户存下来叫什么。"""

    async def _gen() -> AsyncIterator[bytes]:
        yield b"PK"

    response = Served.stream(
        _gen(), "application/zip", filename="产物.zip", size=2
    ).response()

    assert response.status_code == 200
    assert response.media_type == "application/zip"
    assert response.headers["content-length"] == "2"
    # 中文名按 RFC 5987 编码，浏览器保存时才不会变成一串百分号
    assert response.headers["content-disposition"].startswith(
        "attachment; filename*=utf-8''"
    )


def test_library_serve_streams_originals_but_redirects_thumbnails() -> None:
    """原文件（XHR）同源转发；缩略图（`<img>`）仍 307。

    两者共用 `_serve`，所以这条用例真正锁的是那个 `proxy` 分支：把缩略图也改成
    转发，封面就会白白经过应用进程（一屏几十张图）；把原文件改回 307，
    「下载」又会开始报 CORS。
    """
    storage = _RemoteStorage(b"ray")
    # `_serve` 只用 storage，不碰 session
    service = LibraryService(None, storage)  # type: ignore[arg-type]

    async def run() -> tuple[Served, Served]:
        original = await service._serve(  # noqa: SLF001
            "k.jpg", "image/jpeg", proxy=True, filename="a.jpg"
        )
        thumbnail = await service._serve("k.jpg", "image/jpeg")  # noqa: SLF001
        return original, thumbnail

    original, thumbnail = asyncio.run(run())

    assert original.chunks is not None
    assert original.url is None
    assert original.filename == "a.jpg"

    assert thumbnail.chunks is None
    assert thumbnail.url == "https://bucket.example/k.jpg?sig=stub"
