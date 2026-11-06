"""归档存储工厂：与 `get_storage` / `get_queue` 同形态，按配置选实现。

以前这里是 `return LocalArchiveStorage()` —— 一个恒返回同类型的函数，
`.env.example` 里「换实现只改这里」这句注释对归档盘并不成立，
而且 provider 里还有 5 处直接 `LocalArchiveStorage()`，要上 S3 得逐个改。

现在：`ARCHIVE_DRIVER=<name>` → `infra/archive/<name>.py` 的 `Driver`。
业务层只认 `ArchiveStorage` 协议，测试要注入假实现也不必再碰真实目录。
"""

from __future__ import annotations

from functools import lru_cache

from core.config import settings
from infra.archive.base import ArchiveStorage, checksum, lease, read_json, within, write_json
from infra.archive.blobs import BlobStore, blob_key, clip_key, is_blob_key, preview_key
from infra.archive.local import local_storage
from infra.archive.runs import (
    existing_runs,
    index_key,
    lock_path,
    read_pointer,
    run_dir,
    runs_root,
    save_pointer,
)
from infra.loader import load_driver


@lru_cache
def get_archive_storage() -> ArchiveStorage:
    return load_driver("archive", settings.archive_driver)


def reset_archive_storage() -> None:
    """测试用：清空工厂缓存以便切换驱动。"""
    get_archive_storage.cache_clear()


__all__ = [
    "ArchiveStorage",
    "BlobStore",
    "blob_key",
    "checksum",
    "clip_key",
    "existing_runs",
    "get_archive_storage",
    "index_key",
    "is_blob_key",
    "lease",
    "local_storage",
    "lock_path",
    "preview_key",
    "read_json",
    "read_pointer",
    "reset_archive_storage",
    "run_dir",
    "runs_root",
    "save_pointer",
    "within",
    "write_json",
]
