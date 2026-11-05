"""对象存储抽象。

业务层只依赖 `ObjectStorage` 协议，切换本地磁盘 / S3 / OSS 不改业务代码。
新增实现：在 `infra/storage/` 下加文件，然后在 `infra/__init__.py` 的工厂里加分支。
"""

from __future__ import annotations

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
