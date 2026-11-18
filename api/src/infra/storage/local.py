"""本地磁盘存储实现：开发默认。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from core.config import settings
from infra.storage.base import StorageError

# 对象 key 的合法字符集：路径分隔符只允许 `/`，天然挡掉 `C:\`、`..\` 之类
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _temporary_name(target: Path) -> Path:
    """写入用的临时文件名：**同目录** + uuid4。

    - 必须同目录：跨目录 `replace` 在 Windows 上直接失败、POSIX 上退化成
      「先删再拷」，两步之间目标是不存在的，原子性反而丢了。
    - 必须 uuid4：pid 只区分**进程**，同一进程的两个协程经 `to_thread` 是两条
      线程，撞在同一个 pid 上就会互相覆盖（POSIX 上表现为「先完成的 rename 把
      文件搬走，后一个找不到自己的临时文件」）。
    """
    return target.with_name(f"{target.name}.{uuid4().hex}.part")


def _write_bytes_atomic(target: Path, data: bytes) -> None:
    temporary = _temporary_name(target)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_or_digest(source: Path, target: Path) -> str:
    """搬字节；源和目标已经是同一个文件时**只算摘要**。

    `_copy_with_digest` 先把目标截断成 0 再读源，source == target 时读到空文件
    （入库把 `storageKey` 当 source 传回来正是这种情形，实测 1000 字节变 0）。
    """
    if source.resolve() == target:
        return _digest_of(source)
    return _copy_with_digest(source, target)


def _copy_with_digest(source: Path, target: Path) -> str:
    """边写边算摘要，避免同一份文件读两遍。

    写临时文件再 `replace`：`put_file` 搬的是 300MB 成片，直接在目标上 `wb`
    的话，进程被杀会留下半份、两个协程并发会互相截断，而超时被取消的
    `to_thread` 拷贝**停不下来**，下一跳还会再写同一路径。
    """
    temporary = _temporary_name(target)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as src, temporary.open("wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(chunk)
                dst.write(chunk)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return digest.hexdigest()


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        # 不直接写目标：`write_bytes` 先截断再写，中途失败/被杀留下的半份文件
        # 会被 `exists()` 当成"在"，随后被 `/static`、`get()`、`FileResponse`
        # 原样发出去，且没有任何校验会报错。
        await asyncio.to_thread(_write_bytes_atomic, path, data)
        return self.url_for(key)

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError(f"对象不存在: {key}")
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        """本地实现就是问一次文件系统；非法 key 仍由 `_resolve` 抛错。"""
        return await asyncio.to_thread(self._resolve(key).is_file)

    async def delete(self, key: str) -> None:
        """删除对象；不存在时幂等返回（协议承诺）。

        不要写成「先 `exists()` 再 `unlink()`」：这两步之间有一个窗口，并发删除
        （用户连点、重试、两个实例的清理任务同时跑）时第二个调用会删一个**刚刚**
        被别人删掉的文件，抛 `FileNotFoundError`——它不在 `StorageError` 里，
        于是落成 500。`missing_ok` 把「不存在」和「删掉了」合成同一个结果。
        """
        await asyncio.to_thread(self._resolve(key).unlink, missing_ok=True)

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

    async def put_file(
        self, key: str, path: Path, content_type: str = "application/octet-stream"
    ) -> str:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 「source 与 target 是同一个文件」的判断放在线程里做：`resolve()` 是
        # 阻塞调用，而这里已经在协程里了（ASYNC240）。
        return await asyncio.to_thread(_copy_or_digest, path, target)

    async def presigned_download_url(self, key: str, *, expires_in: int = 900) -> str:
        """本地没有签名语义：`/static` 是公开前缀，鉴权只能由路由层做。"""
        return self.url_for(key)

    def local_path(self, key: str) -> Path | None:
        """本地对象本来就在盘上：直出路径，让 `FileResponse` 支持 Range。"""
        path = self._resolve(key)
        return path if path.is_file() else None

    def iter_chunks(self, key: str, *, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        """按块读盘。key 与存在性在这里就判掉：本地校验零成本，不必拖到迭代时才报。"""
        path = self._resolve(key)
        if not path.is_file():
            raise StorageError(f"对象不存在: {key}")
        return self._read_chunks(path, chunk_size)

    @staticmethod
    async def _read_chunks(path: Path, chunk_size: int) -> AsyncIterator[bytes]:
        """读句柄放线程池里开、读、关：`open()` 与 `read()` 都是阻塞调用，
        在事件循环里直接跑会把这个 worker 上其他请求一起卡住（下载大文件时最明显）。"""
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    return
                yield chunk
        finally:
            # 客户端中途断开时 `StreamingResponse` 会 close 生成器走这里；
            # 不关句柄就会一直占着文件锁（Windows 上重命名 / 删除会失败）
            await asyncio.to_thread(handle.close)




# 驱动解析约定：infra.loader 通过这个名字拿到实现
Driver = LocalStorage
