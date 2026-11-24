"""通道层 OSS 落地的单元测试。

这里测的是**供应商中立**的那一半：怎么传、传成什么链接、字节从哪来。
供应商特有的部分（怎么签发上传凭据）由驱动负责，不在这里。
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from storage_stub import DirStorage

from infra.oss_channel import (
    OssUploadTarget,
    load_media_bytes,
    upload_media_bytes,
)
from infra.storage.base import StorageError


def _target(host: str = "https://upload.example.com") -> OssUploadTarget:
    return OssUploadTarget(
        upload_host=host,
        fields={"OSSAccessKeyId": "id", "Signature": "sig", "policy": "p"},
        key_prefix="dashscope-instant/test",
    )


def test_object_key_lands_under_the_prefix_and_keeps_the_suffix() -> None:
    key = _target().object_key("a.mp4")
    assert key.startswith("dashscope-instant/test/")
    assert key.endswith(".mp4"), "上游按后缀判类型，后缀不能丢"
    assert key != _target().object_key("a.mp4"), "两个素材不能撞同一个对象 key"


async def test_upload_posts_the_form_and_returns_an_oss_link() -> None:
    seen: dict[str, bytes] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["url"] = str(request.url).encode()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await upload_media_bytes(
            b"payload-bytes",
            kind="image",
            filename="1.png",
            target=_target(),
            timeout_sec=5.0,
            client=client,
        )

    assert result.kind == "image"
    assert result.url.startswith("oss://dashscope-instant/test/")
    assert result.url.endswith(".png")
    assert result.sha256 == hashlib.sha256(b"payload-bytes").hexdigest()
    assert result.size == len(b"payload-bytes")
    assert seen["url"].startswith(b"https://upload.example.com")
    body = seen["body"]
    for field in (b'name="OSSAccessKeyId"', b'name="key"', b'name="success_action_status"'):
        assert field in body, field
    assert b"payload-bytes" in body, "文件内容要真的在表单里"


async def test_upload_refuses_a_plain_http_endpoint() -> None:
    with pytest.raises(ValueError):
        await upload_media_bytes(
            b"x",
            kind="image",
            filename="1.png",
            target=_target(host="http://upload.example.com"),
            timeout_sec=5.0,
        )


async def test_load_media_bytes_reads_from_our_storage(monkeypatch, tmp_path) -> None:
    (tmp_path / "a.bin").write_bytes(b"hello")
    monkeypatch.setattr("infra.get_storage", lambda: DirStorage(tmp_path))

    assert await load_media_bytes("a.bin") == b"hello"


async def test_load_media_bytes_reports_a_missing_object(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("infra.get_storage", lambda: DirStorage(tmp_path))

    with pytest.raises(StorageError):
        await load_media_bytes("nope.bin")


async def test_load_media_bytes_refuses_an_empty_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("infra.get_storage", lambda: DirStorage(tmp_path))

    with pytest.raises(StorageError):
        await load_media_bytes("")
