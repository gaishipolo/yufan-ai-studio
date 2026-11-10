"""对象存储抽象。

业务层只依赖 `ObjectStorage` 协议，切换本地磁盘 / S3 / OSS 不改业务代码。
新增实现：在 `infra/storage/` 下加文件（暴露 `Driver`），再用 `STORAGE_DRIVER` 选它。
本文件与 `infra/__init__.py` 都**不含任何 if/elif**：按名加载在 `infra/loader.py`
的 `load_driver()`，照旧注释去改工厂只会白改一处。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, runtime_checkable


class StorageError(Exception):
    """存储层异常，由中间件转换为 502。"""


@runtime_checkable
class ObjectStorage(Protocol):
    """对象存储最小接口。"""

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        """写入对象，返回可访问 URL。"""
        ...

    async def get(self, key: str) -> bytes:
        """读取对象内容。"""
        ...

    async def delete(self, key: str) -> None:
        """删除对象（不存在时幂等返回）。"""
        ...

    async def exists(self, key: str) -> bool:
        """对象在不在。**不要用 `get()` 代替**：那会把整个对象拉进内存。

        存在的理由只有一个：`library repreview` 这类「记录在、字节不在」的
        对账扫描要逐条问一遍，而封面的字节往往和原图差两三个数量级，
        拿 `get()` 探一次能顶几十次 `head`。

        语义与 `delete` 相反：非空 key 但对象不存在 → `False`；
        key 本身非法（含 `..`、绝对路径）→ 抛 `StorageError`，别把「路径有问题」
        混进「东西不在」。
        """
        ...

    def url_for(self, key: str) -> str:
        """根据 key 生成公开访问 URL。"""
        ...

    async def presigned_upload_url(
        self, key: str, *, content_type: str = "application/octet-stream", expires_in: int = 900
    ) -> str:
        """签发前端直传地址。本地实现返回内部上传端点。"""
        ...

    async def put_file(
        self, key: str, path: Path, content_type: str = "application/octet-stream"
    ) -> str:
        """从本地文件写入，返回内容的 sha256 十六进制摘要。

        单独一个方法而不是让调用方 `path.read_bytes()` 再 `put()`：素材上限 50MB、
        归档原片 300MB，整块读进内存的峰值是「并发数 × 文件大小」，S3 驱动还要
        多一次完整拷贝。实现方自己决定流式还是分片，调用方只看得到摘要。
        """
        ...

    async def presigned_download_url(self, key: str, *, expires_in: int = 900) -> str:
        """签发私有对象的临时下载地址（读方向）。

        本地实现没有签名语义，返回静态 URL；调用方不要假设它一定带时效。
        """
        ...

    def local_path(self, key: str) -> Path | None:
        """能直出磁盘路径的实现返回 `Path`，远程实现返回 `None`。

        存在的唯一理由：`FileResponse` 靠真实路径支持 HTTP Range，视频才能拖进度条。
        远程驱动不该把字节搬进应用进程，调用方拿到 `None` 时改用预签名地址重定向。
        不要为了「抽象干净」把它拆成两次探测 —— 这本来就是「能不能零拷贝」的能力询问。
        """
        ...

    def iter_chunks(self, key: str, *, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        """按块读出对象内容（异步生成器），不把整个文件塞进内存。

        与 `get()` 的分工：
        - `get()` 是「我要这份字节来做点什么」（解析、校验、算出摘要），调用方本来
          就需要全部内容；
        - `iter_chunks()` 是「我要把它转手交出去」（HTTP 响应、写进另一个对象）。
          同一个 300MB 的文件走 `get()` 会在应用里留一份完整副本，并发几个就 OOM。

        **为什么要这个方法**：下载 zip 走不了预签名重定向。前端是用 XHR 取 blob
        再触发保存的（`saveBlob`），而 `307` 到对象存储会让浏览器发起一个跨域请求，
        目标必须回 CORS 头；MinIO 当前的 `PutBucketCors` 直接回 NotImplemented，
        这条链路就变成了「控制台报红、功能时好时坏」。改成同源转发后不再依赖任何
        桶配置，私有桶也能用。

        出错（对象不存在 / 网络失败）抛 `StorageError`。**抛出时机由实现决定**：
        本地驱动在调用时就校验（不额外成本），远端驱动要等首次迭代（不为此多打
        一次 HEAD）。消费方不要假设「拿到返回值就代表字节一定存在」。
        """
        ...
