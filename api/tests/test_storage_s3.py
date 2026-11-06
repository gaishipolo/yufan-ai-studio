"""S3 驱动的协议行为测试：不连真实对象存储，避免 CI 依赖容器。

真实 S3/MinIO 的连通性由部署后冒烟覆盖；这里只守「调用形状、key 规范化、
异常包装、摘要计算」这些改驱动时最容易写错的地方。
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from infra.storage.base import StorageError
from infra.storage.s3 import S3Settings, S3Storage


class _FakeClient:
    """只实现驱动实际用到的方法，签名与 boto3 保持一致（关键字参数）。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.presigned: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop(Key, None)

    def upload_file(
        self, path: str, bucket: str, key: str, ExtraArgs: dict[str, str] | None = None
    ) -> None:
        self.objects[key] = Path(path).read_bytes()

    def generate_presigned_url(self, operation: str, *, Params: dict, ExpiresIn: int) -> str:
        url = f"http://minio/{Params['Bucket']}/{Params['Key']}?op={operation}&t={ExpiresIn}"
        self.presigned.append(url)
        return url


def _build(monkeypatch: pytest.MonkeyPatch, bucket: str = "media") -> tuple[S3Storage, _FakeClient]:
    fake = _FakeClient()
    monkeypatch.setattr("infra.storage.s3.boto3.client", lambda *a, **k: fake)
    settings = S3Settings(
        bucket=bucket,
        endpoint="http://127.0.0.1:9000",
        access_key="ak",
        secret_key="sk",
    )
    return S3Storage(settings), fake


def test_put_and_get_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    storage, fake = _build(monkeypatch)

    url = asyncio_run(storage.put("assets/a.png", b"hello", "image/png"))

    assert url == "http://127.0.0.1:9000/media/assets/a.png"
    assert fake.objects["assets/a.png"] == b"hello"
    assert asyncio_run(storage.get("assets/a.png")) == b"hello"


def test_put_file_returns_sha256(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, fake = _build(monkeypatch)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"binary-body")

    digest = asyncio_run(storage.put_file("library/clip.mp4", source, "video/mp4"))

    assert digest == hashlib.sha256(b"binary-body").hexdigest()
    assert fake.objects["library/clip.mp4"] == b"binary-body"


def test_delete_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    storage, fake = _build(monkeypatch)
    asyncio_run(storage.put("assets/b.png", b"x"))
    asyncio_run(storage.delete("assets/b.png"))
    asyncio_run(storage.delete("assets/b.png"))
    assert "assets/b.png" not in fake.objects


def test_presigned_urls_carry_bucket_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    storage, fake = _build(monkeypatch)

    upload = asyncio_run(storage.presigned_upload_url("assets/c.png", expires_in=60))
    download = asyncio_run(storage.presigned_download_url("assets/c.png"))

    assert "media/assets/c.png" in upload
    assert "op=put_object" in upload
    assert "op=get_object" in download
    assert len(fake.presigned) == 2


@pytest.mark.parametrize("key", ["", "../etc/passwd", "/./x"])
def test_rejects_illegal_keys(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    storage, _ = _build(monkeypatch)
    with pytest.raises(StorageError):
        asyncio_run(storage.get(key))


def test_requires_bucket() -> None:
    with pytest.raises(StorageError, match="S3_BUCKET"):
        S3Storage(S3Settings(bucket="", endpoint="http://x", access_key="a", secret_key="b"))


def asyncio_run(coroutine: Any) -> Any:
    """测试内同步执行协程；pytest 用 asyncio_mode=auto，这里只是想少写 async 关键字。"""
    import asyncio

    return asyncio.run(coroutine)
