"""S3 / MinIO 对象存储驱动。

用 `boto3` + `asyncio.to_thread`，不用 `aioboto3`：`archive_service` 里已经是
「同步 IO 丢线程池」的写法，同步 SDK 更成熟；aiobotocore 与 botocore 版本绑定很紧，
升级时容易连带冲突。

key 由调用方决定（`library/**` 归档历史、`assets/**` 新素材），驱动不关心业务分类。
MinIO 必须用 path-style 寻址，否则虚拟主机风格会把桶名当域名解析。

已知边界：
- `presigned_upload_url` 直传要求桶配了 CORS PUT；内网 MinIO 默认不带，
  没配之前浏览器会被拦下，退回 `put_file` 走服务端中转。
- `url_for` 假设桶对读公开；私有桶必须用 `presigned_download_url`。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from core.logging import get_logger
from infra.blob import s3_client
from infra.driver_config import DriverSettings, driver_config
from infra.storage.base import StorageError

logger = get_logger(__name__)


class S3Settings(DriverSettings):
    """S3 部署参数，环境变量前缀 `S3_`。

    `endpoint` 留空表示 AWS 官方；自建 MinIO 填 `http://10.x.x.x:9000`。
    生产必须显式四项，不允许「跑起来才发现写进了某个默认桶」。
    """

    model_config = driver_config("S3")

    # 存储有 local / s3 两个实现：只有 STORAGE_DRIVER=s3 时，下面四项才是必填。
    # 不声明 kind 的话，一个用 local 的生产环境会因为没配 S3_ENDPOINT 而启动失败。
    driver_kind: ClassVar[str] = "storage"
    driver_name: ClassVar[str] = "s3"

    endpoint: str = ""
    bucket: str = ""
    access_key: str = ""
    secret_key: str = ""
    region: str = "us-east-1"
    use_ssl: bool = True
    verify: bool = True

    prod_required_fields: ClassVar[tuple[str, ...]] = (
        "endpoint",
        "bucket",
        "access_key",
        "secret_key",
    )


@lru_cache
def get_s3_settings() -> S3Settings:
    return S3Settings()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_not_found(exc: ClientError) -> bool:
    """S3 / MinIO 的「没有这个 key」。

    按状态码判而不是按 Error.Code 字符串：AWS 回 `NoSuchKey`，有的实现回 `404`，
    老版本 MinIO 对 HEAD 只给 `NotFound`。403 不能算进来——那是权限问题，
    「问不到」与「不存在」混淆过一次就会让对账扫描整批重建。
    """
    response = exc.response or {}
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    code = (response.get("Error") or {}).get("Code")
    return status == 404 or code in {"NoSuchKey", "NotFound", "404"}


class S3Storage:
    def __init__(self, settings: S3Settings | None = None) -> None:
        self.settings = settings or get_s3_settings()
        if not self.settings.bucket:
            raise StorageError("S3_BUCKET 未配置，无法使用 S3 驱动")
        # 与归档通路共用同一个 client：连接参数相同就命中 `infra.blob` 的缓存。
        self._client = s3_client(
            endpoint=self.settings.endpoint,
            access_key=self.settings.access_key,
            secret_key=self.settings.secret_key,
            region=self.settings.region,
            use_ssl=self.settings.use_ssl,
            verify=self.settings.verify,
            factory=boto3.client,
        )

    # ------------------------------------------------------------------ 写
    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        key = self._key(key)
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self.settings.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"S3 写入失败（{key}）：{exc}") from exc
        return self.url_for(key)

    async def put_file(
        self, key: str, path: Path, content_type: str = "application/octet-stream"
    ) -> str:
        """分片上传，同时算 sha256；大文件不进内存。"""
        key = self._key(key)
        digest = await asyncio.to_thread(_digest, path)
        try:
            await asyncio.to_thread(
                self._client.upload_file,
                str(path),
                self.settings.bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"S3 写入失败（{key}）：{exc}") from exc
        return digest

    async def delete(self, key: str) -> None:
        """S3 的 delete 对不存在的 key 也返回 204，天然幂等。"""
        key = self._key(key)
        try:
            await asyncio.to_thread(
                self._client.delete_object, Bucket=self.settings.bucket, Key=key
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"S3 删除失败（{key}）：{exc}") from exc

    # ------------------------------------------------------------------ 读
    async def exists(self, key: str) -> bool:
        """`head_object`，不读 body。

        只有 `404 / NoSuchKey`（各家实现写法不同，按状态码判最稳）算「不在」；
        403（没权限）、502（网关）必须抛出去——把「问不到」当成「不存在」，
        对账命令会去重建一批其实好好的对象，反而把问题盖住。
        """
        key = self._key(key)

        def _head() -> None:
            self._client.head_object(Bucket=self.settings.bucket, Key=key)

        try:
            await asyncio.to_thread(_head)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise StorageError(f"S3 探测失败（{key}）：{exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"S3 探测失败（{key}）：{exc}") from exc
        return True

    async def get(self, key: str) -> bytes:
        key = self._key(key)

        def _read() -> bytes:
            response = self._client.get_object(Bucket=self.settings.bucket, Key=key)
            return response["Body"].read()

        try:
            return await asyncio.to_thread(_read)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"S3 读取失败（{key}）：{exc}") from exc

    def url_for(self, key: str) -> str:
        key = self._key(key)
        if self.settings.endpoint:
            return f"{self.settings.endpoint.rstrip('/')}/{self.settings.bucket}/{key}"
        return f"https://{self.settings.bucket}.s3.{self.settings.region}.amazonaws.com/{key}"

    # ------------------------------------------------------------------ 签名
    async def presigned_upload_url(
        self, key: str, *, content_type: str = "application/octet-stream", expires_in: int = 900
    ) -> str:
        key = self._key(key)
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "put_object",
            Params={
                "Bucket": self.settings.bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    async def presigned_download_url(self, key: str, *, expires_in: int = 900) -> str:
        key = self._key(key)
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.settings.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def local_path(self, key: str) -> Path | None:
        """对象在桶里，没有本地路径：调用方应改用 `presigned_download_url` 重定向。

        这也是 S3 版本比本地版本更省的原因——字节不经过应用进程，Range 由对象存储
        自己处理（拖动视频进度条直接命中桶，不需要先下载整个文件）。
        """
        return None

    def iter_chunks(self, key: str, *, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        """流式读对象。

        `StreamingBody.read(n)` 是同步阻塞的，所以每一块都要丢线程池；整块 `read()`
        就是 `get()`，那一条已经存在（要全部内容时用它，别用这个）。

        **不额外打 HEAD 做存在性校验**：那要多一次往返，而 key 是我们自己写进去的，
        真丢了属于部署事故。代价是「对象不存在」会在首次迭代时抛——此时 200 已经
        发出去了，前端看到的是连接中断。宁可这样，也不让每次下载都多一跳。
        """
        return self._read_object(self._key(key), chunk_size)

    async def _read_object(self, key: str, chunk_size: int) -> AsyncIterator[bytes]:
        def _open() -> Any:
            return self._client.get_object(Bucket=self.settings.bucket, Key=key)["Body"]

        try:
            body = await asyncio.to_thread(_open)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"S3 读取失败（{key}）：{exc}") from exc
        try:
            while True:
                chunk = await asyncio.to_thread(body.read, chunk_size)
                if not chunk:
                    return
                yield chunk
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"S3 读取中断（{key}）：{exc}") from exc
        finally:
            # 客户端断开 / 生成器被 close 时都要释放连接，否则连接池会被慢慢耗尽
            await asyncio.to_thread(body.close)

    @staticmethod
    def _key(key: str) -> str:
        normalized = key.strip().lstrip("/")
        if not normalized or any(part in (".", "..") for part in normalized.split("/")):
            raise StorageError(f"非法的对象路径: {key}")
        return normalized


# 驱动解析约定：infra.loader 通过这个名字拿到实现
Driver = S3Storage
