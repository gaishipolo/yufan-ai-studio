"""对象存储抽象。

业务层只依赖 `ObjectStorage` 协议，切换本地磁盘 / S3 / OSS 不改业务代码。
新增实现：在 `infra/storage/` 下加文件，然后在 `infra/__init__.py` 的工厂里加分支。
"""

from __future__ import annotations

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
