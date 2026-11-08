"""归档素材的「彻底删除」：DB 行 + JSON 镜像 + 没人再引用的字节，一次做完。

为什么单独一个模块：`DELETE /assets/{id}` 和 `app prune-blobs
--include-trash` 要做的是同一件事，两处各写一遍一定会漂移——最危险的漂移是
「一处查了引用、另一处没查」。审计留痕也放在这里，两条入口都覆盖到。

## 字节为什么按摘要判断能不能删

内容寻址下 blob、缩略图、转码流三个 key 全部由 sha256 派生，所以「同一份字节」
可能被另一个人、另一个分类、甚至另一个来源的行共享。判断口径不是「这个 key
字符串在不在别的行」，而是**还有没有别的行持有同一个摘要**（历史行没记摘要时
退回精确匹配 storage_key / cover_key）。漏判一次就是别人的素材裂图。
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.exceptions import BadRequest
from core.logging import get_logger
from core.ports.assets import ask_asset_references
from infra.archive import ArchiveStorage, clip_key, get_archive_storage, preview_key
from modules.asset.errors import AssetInUse
from modules.asset.mirror import MirrorProjector
from modules.asset.models import Asset
from modules.asset.schema import AssetSource

__all__ = ["candidate_keys", "purge_trash_asset"]

logger = get_logger(__name__)
_SHA = re.compile(r"[0-9a-f]{64}")


def candidate_keys(row: Asset) -> list[str]:
    """这条记录可能留下的所有字节（去重、去掉空值）。"""
    keys = [row.storage_key, row.cover_key]
    digest = row.sha256
    if digest and _SHA.fullmatch(digest):
        # 缩略图与转码流是按需派生的，库里不一定记了它们
        keys.extend((preview_key(digest), clip_key(digest)))
    return list(dict.fromkeys(key for key in keys if key))


async def _bytes_still_used(session: AsyncSession, key: str, digest: str | None) -> bool:
    conditions = [Asset.storage_key == key, Asset.cover_key == key]
    if digest:
        conditions.append(Asset.sha256 == digest)
    row = await session.scalar(select(Asset.id).where(or_(*conditions)).limit(1))
    return row is not None


async def purge_trash_asset(
    session: AsyncSession,
    row: Asset,
    storage: ArchiveStorage | None = None,
    *,
    actor_id: str | None = None,
    action: str = "asset.archive.purge",
    dry_run: bool = False,
) -> list[str]:
    """彻底删除一条**已在回收站**的归档素材，返回实际（或预计）删掉的字节 key。

    `dry_run` 仍然会做引用检查——CLI 的报告必须和 `--execute` 看到的是同一批，
    否则「先看一眼没问题、真跑却拦下一半」比没有预演更糟。

    顺序是「先查引用 → 再提交删行 → 最后删字节」：反过来的话，字节删了而事务
    回滚，列表还在却已经 404。删字节失败不回滚事务（留个孤儿，GC 能扫）。
    """
    if row.source != AssetSource.ARCHIVE:
        raise BadRequest("只能清空归档素材的回收站")
    if row.deleted_at is None:
        raise BadRequest("请先将素材移入回收站，再彻底删除")

    references = await ask_asset_references([row.id])
    if references.get(row.id):
        raise AssetInUse(details={"references": list(references[row.id])})

    drive = storage if storage is not None else get_archive_storage()
    keys = candidate_keys(row)
    if dry_run:
        return keys
    asset_id, name, digest = row.id, row.name, row.sha256
    deleted_at = row.deleted_at

    # DB 先提交，再删派生镜像：反过来会出现「事务回滚了、worker 先读不到」。
    # 删镜像失败只是留下孤儿，`asset.mirror` 周期对账会按 DB 清单清掉。
    await session.delete(row)
    await session.commit()

    try:
        await asyncio.to_thread(MirrorProjector(drive).remove, asset_id)
    except Exception as exc:  # noqa: BLE001 - 行已删，镜像交给对账收敛
        logger.warning("彻底删除后镜像清理失败，等待周期对账：id=%s err=%s", asset_id, exc)
    removed: list[str] = []
    for key in keys:
        if await _bytes_still_used(session, key, digest):
            continue
        await asyncio.to_thread(drive.remove, key)
        removed.append(key)

    await audit.record(
        action,
        actor_id=actor_id,
        target_type="asset",
        target_id=asset_id,
        detail={
            "name": name,
            "source": "archive",
            "deletedAt": deleted_at.isoformat() if deleted_at else None,
            "purgedAt": datetime.now(UTC).isoformat(),
            "bytesRemoved": removed,
        },
    )
    return removed
