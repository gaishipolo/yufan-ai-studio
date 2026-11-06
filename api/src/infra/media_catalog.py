"""归档索引与发布版本的文件协议；权限由调用业务层检查。"""

from __future__ import annotations

import re
from contextlib import AbstractContextManager
from pathlib import Path

from core.exceptions import BadRequest, NotFound
from core.media import MediaPack, StoredMedia
from infra.storage.archive import ArchiveStorage, get_archive_storage, lease


def catalog_lease(storage: ArchiveStorage) -> AbstractContextManager[None]:
    """短期串行化分享撤回、发布、准备快照与最终提交授权。"""
    return lease(storage.resolve("catalog/locks/access.lease"))


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        raise NotFound("记录不存在")
    return value


class MediaCatalog:
    def __init__(self, storage: ArchiveStorage | None = None) -> None:
        self.storage = storage or get_archive_storage()

    def media(self, uid: str) -> StoredMedia:
        path = self.storage.resolve(f"catalog/assets/{safe_id(uid)}.json")
        if not path.is_file():
            raise NotFound("素材不存在")
        return StoredMedia.model_validate(self.storage.read(path))

    def source(self, uid: str, owner: str) -> StoredMedia:
        item = self.media(uid)
        if (
            item.deleted_at
            or item.category != "source"
            or (item.owner_id != owner and not item.shared)
        ):
            raise NotFound("原片不存在或已停止共享")
        return item

    def pack(self, uid: str, version: int | None = None) -> MediaPack:
        path = self.storage.resolve(f"catalog/packs/{safe_id(uid)}.json")
        if not path.is_file():
            raise NotFound("素材包不存在")
        head = MediaPack.model_validate(self.storage.read(path))
        if version is None or head.version == version:
            return head
        path = self.storage.resolve(f"catalog/pack_versions/{safe_id(uid)}/{version}.json")
        if version < 1 or not path.is_file():
            raise NotFound("发布版本不存在")
        item = MediaPack.model_validate(self.storage.read(path))
        if head.status != "published":
            item.status = head.status
        return item

    def published(self, uid: str, version: int | None = None) -> MediaPack:
        pack = self.pack(uid, version)
        if pack.status != "published" or pack.version < 1:
            raise BadRequest("素材包未发布或已停用，请重新选择")
        return pack

    def references(self, pack: MediaPack) -> list[StoredMedia]:
        ids = pack.reference_ids
        if not 1 <= len(ids) <= 10 or len(set(ids)) != len(ids):
            raise BadRequest("素材包需要 1–10 张不重复的参考图")
        if not pack.name.strip() or pack.cover_reference_id not in ids:
            raise BadRequest("请填写名称并选择包内主图")
        rows = [self.media(uid) for uid in ids]
        if any(
            m.deleted_at
            or m.owner_id != pack.owner_id
            or m.category != pack.kind
            or m.type != "image"
            for m in rows
        ):
            raise BadRequest("素材包引用缺失、归属或分类不匹配")
        return rows

    def check_run(self, path: Path) -> None:
        """只校验尚未提交的任务；已有上游回执继续查询。"""
        meta = self.storage.read(path / "run.json")
        if not isinstance(meta, dict):
            raise BadRequest("调用记录无效")
        if meta.get("task_id") or meta.get("taskId"):
            return
        config_path = path / "inputs/configuration.json"
        if not config_path.is_file():
            raise BadRequest("历史任务缺少调用授权，请重新准备")
        config = self.storage.read(config_path)
        if not isinstance(config, dict):
            raise BadRequest("调用配置无效")
        self.source(
            str(config.get("sourceId", config.get("source_id", ""))),
            str(meta.get("owner_id", meta.get("ownerId", ""))),
        )
        uid = config.get("packId", config.get("pack_id"))
        if not uid:
            raise BadRequest("请选择专员发布的素材包后重新准备")
        version = config.get("packVersion", config.get("pack_version"))
        if not isinstance(version, int) or version < 1:
            raise BadRequest("历史任务缺少发布版本，请重新准备")
        self.published(str(uid), version)
