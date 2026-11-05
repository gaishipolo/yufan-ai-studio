"""本地磁盘存储实现：开发默认。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from core.config import settings
from infra.storage.base import StorageError

# 对象 key 的合法字符集：路径分隔符只允许 `/`，天然挡掉 `C:\`、`..\` 之类
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class LocalStorage:
    def __init__(self, root: str | None = None, public_base: str | None = None) -> None:
        self.root = Path(root or settings.storage_local_root).resolve()
        self.public_base = (public_base or settings.storage_public_base).rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        """把对象 key 解析成磁盘路径，非法 key 一律拒绝。

        两道防线：
        1. key 只允许 `[A-Za-z0-9._/-]` 且不含 `..` 段（业务层之前就挡住）
        2. 解析后必须真的位于 root 之下 —— 必须用 `is_relative_to`，
           字符串前缀判断会被 `../storage-evil/x` 这类同前缀目录绕过
        """
        normalized = key.strip().lstrip("/")
        if not normalized or not _KEY_RE.match(normalized):
            raise StorageError(f"非法的对象路径: {key}")
        if any(part in (".", "..") for part in normalized.split("/")):
            raise StorageError(f"非法的对象路径: {key}")

        target = (self.root / normalized).resolve()
        if target == self.root or not target.is_relative_to(self.root):
            raise StorageError(f"非法的对象路径: {key}")
        return target

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return self.url_for(key)

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError(f"对象不存在: {key}")
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)

    def url_for(self, key: str) -> str:
        return f"{self.public_base}/{key.lstrip('/')}"

    async def presigned_upload_url(
        self, key: str, *, content_type: str = "application/octet-stream", expires_in: int = 900
    ) -> str:
        """本地实现没有直传端点，显式失败好过返回一个 404 死链接。

        需要浏览器直传时改用 S3/OSS 驱动（`STORAGE_DRIVER=s3`），
        或在接入上传接口后再实现本地版本。
        """
        raise StorageError("本地存储不支持预签名直传，请改用 put() 写入")


# 驱动解析约定：infra.loader 通过这个名字拿到实现
Driver = LocalStorage
