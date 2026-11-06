"""S3 驱动的真实连通性冒烟（对 MinIO 打真 HTTP）。

单测 `test_storage_s3.py` 用 fake client，验证的是「我们怎么调 boto3」；
这里验证的是「boto3 和真实服务端之间真的能通」——签名版本、path-style 寻址、
分片上传、预签名 URL 的有效性，这几样 fake client 全都覆盖不到。

默认跳过，因为它需要一个跑着的对象存储。本地：

    docker run -d --name yufan-minio -p 19000:9000 -p 19001:9001 \\
        -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin123 \\
        elestio/minio:latest server /data --console-address ":9001"

    S3_LIVE_ENDPOINT=http://127.0.0.1:19000 \\
    S3_LIVE_ACCESS_KEY=minioadmin \\
    S3_LIVE_SECRET_KEY=minioadmin123 \\
    pytest tests/test_storage_s3_live.py -q

CI 里起一个 MinIO service 容器，导出上面三个变量就会被执行。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import boto3
import httpx
import pytest

from infra.storage.base import StorageError
from infra.storage.s3 import S3Settings, S3Storage

ENDPOINT = os.getenv("S3_LIVE_ENDPOINT", "")
BUCKET = os.getenv("S3_LIVE_BUCKET", "yufan-test")

pytestmark = pytest.mark.skipif(
    not ENDPOINT,
    reason="未配置 S3_LIVE_ENDPOINT，跳过真实对象存储冒烟",
)


@pytest.fixture(scope="module")
def settings() -> S3Settings:
    return S3Settings(
        endpoint=ENDPOINT,
        bucket=BUCKET,
        access_key=os.getenv("S3_LIVE_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("S3_LIVE_SECRET_KEY", "minioadmin123"),
        region="us-east-1",
        use_ssl=ENDPOINT.startswith("https"),
        verify=False,
    )


@pytest.fixture(scope="module", autouse=True)
def _bucket(settings: S3Settings) -> None:
    """桶不存在就建，已存在忽略（MinIO 的 create_bucket 幂等性不如 S3）。"""
    client = boto3.client(
        "s3",
        endpoint_url=settings.endpoint,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name=settings.region,
        use_ssl=settings.use_ssl,
        verify=settings.verify,
    )
    try:
        client.head_bucket(Bucket=settings.bucket)
    except Exception:  # noqa: BLE001 - 任何「取不到」都当作需要新建
        client.create_bucket(Bucket=settings.bucket)


@pytest.fixture
def storage(settings: S3Settings) -> S3Storage:
    return S3Storage(settings)


@pytest.fixture
async def key(storage: S3Storage) -> AsyncIterator[str]:
    """每次用独立前缀（测试可并发），结束后删掉，不在桶里堆垃圾。"""
    prefix = f"assets/live-test/{uuid.uuid4().hex}"
    yield prefix
    with contextlib.suppress(StorageError):
        await storage.delete(prefix)


async def test_put_get_roundtrip(storage: S3Storage, key: str) -> None:
    url = await storage.put(key, b"hello minio", "text/plain")
    assert url.endswith(f"{BUCKET}/{key}")
    assert await storage.get(key) == b"hello minio"


async def test_put_file_returns_sha256_matching_content(
    storage: S3Storage, key: str, tmp_path: Path
) -> None:
    payload = os.urandom(1024)
    local = tmp_path / "a.bin"
    local.write_bytes(payload)

    digest = await storage.put_file(key, local, "application/octet-stream")

    assert digest == hashlib.sha256(payload).hexdigest()
    assert await storage.get(key) == payload


async def test_put_file_large_uses_multipart(storage: S3Storage, key: str, tmp_path: Path) -> None:
    """跨过 8MB 分片阈值，验证 upload_file 的并发分片路径在 MinIO 上能收敛。"""
    payload = os.urandom(9 * 1024 * 1024)
    local = tmp_path / "big.bin"
    local.write_bytes(payload)

    digest = await storage.put_file(key, local, "application/octet-stream")

    assert digest == hashlib.sha256(payload).hexdigest()
    assert len(await storage.get(key)) == len(payload)


async def test_presigned_upload_url_accepts_put(storage: S3Storage, key: str) -> None:
    """预签名直传：签名里带了 ContentType，客户端必须发同样的头，否则 403。"""
    url = await storage.presigned_upload_url(key, content_type="image/png")

    async with httpx.AsyncClient() as http:
        response = await http.put(url, content=b"png-bytes", headers={"Content-Type": "image/png"})

    assert response.status_code == 200, response.text
    assert await storage.get(key) == b"png-bytes"


async def test_presigned_download_url_serves_object(storage: S3Storage, key: str) -> None:
    await storage.put(key, b"download-me", "text/plain")
    url = await storage.presigned_download_url(key)

    async with httpx.AsyncClient() as http:
        response = await http.get(url)

    assert response.status_code == 200
    assert response.content == b"download-me"


async def test_private_bucket_direct_url_is_not_public(storage: S3Storage, key: str) -> None:
    """url_for 是「桶对读公开」的假设；私有桶直连必须被拒，回归时能立刻发现这个假设变了。"""
    await storage.put(key, b"secret", "text/plain")

    async with httpx.AsyncClient() as http:
        response = await http.get(storage.url_for(key))

    assert response.status_code == 403


async def test_delete_is_idempotent_and_object_gone(storage: S3Storage, key: str) -> None:
    await storage.put(key, b"bye", "text/plain")

    await storage.delete(key)
    await storage.delete(key)  # 再删一次不应抛

    with pytest.raises(StorageError):
        await storage.get(key)


@pytest.mark.parametrize("bad_key", ["", "   ", "assets/../etc/passwd", "/./x"])
async def test_rejects_path_traversal(storage: S3Storage, bad_key: str) -> None:
    with pytest.raises(StorageError):
        await storage.put(bad_key, b"x")


async def test_missing_bucket_fails_loudly(settings: S3Settings) -> None:
    """桶名写错不能静默成功，否则素材会「上传成功但找不到」。"""
    broken = settings.model_copy(update={"bucket": "definitely-not-a-bucket"})

    with pytest.raises(StorageError, match="S3 写入失败"):
        await S3Storage(broken).put("assets/x.png", b"x")
