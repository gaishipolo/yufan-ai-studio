"""归档索引投影：**DB 是唯一事实来源**，`catalog/assets/*.json` 是派生视图。

替换模板的 worker 是同步代码，够不着异步 ORM，只能读 JSON。所以镜像必须存在，
但它不该是第二份真相。以前三条写路径各按各的顺序双写（镜像→DB、flush→镜像→commit、
commit→删镜像），中途失败会留「有行没镜像」或孤儿字节，且**没有任何反向重建**。

现在统一为：

1. 业务事务只写 DB，`commit` 成功之后才投影镜像；
2. 投影是**幂等**的：镜像里带 `stateVersion`，对账时版本相同就不动；
3. 投影失败不回滚已提交的事务，也不让请求 500——`@periodic("asset.mirror")`
   与 `app rebuild-mirrors` 会按 DB 重放，补缺、更新落后版本、删掉孤儿镜像；
4. 因此漂移不再是「需要人工核对的事故」，而是**可自动收敛的暂态**。

这套设计不需要 outbox 表或新迁移：`state_version` 本身就是待投影标记，
DB 行集合本身就是权威清单。少一张表就少一处双写。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from infra.archive import ArchiveStorage, get_archive_storage
from modules.asset.archive_repository import ArchiveAssetRepository
from modules.asset.models import Asset
from modules.asset.schema import AssetSource

logger = get_logger(__name__)

MIRROR_PREFIX = "catalog/assets"

__all__ = [
    "MIRROR_PREFIX",
    "MirrorProjector",
    "MirrorReport",
    "MirrorRow",
    "project_after_commit",
    "run_mirror_reconcile",
]


@dataclass(frozen=True, slots=True)
class MirrorRow:
    """一行 DB 记录的投影快照：纯数据，可以安全跨线程交给同步存储驱动。"""

    asset_id: str
    version: int
    payload: dict[str, Any]


@dataclass
class MirrorReport:
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    unchanged: int = 0

    @property
    def healthy(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        return (
            f"重建 {len(self.written)} 份、清理孤儿 {len(self.removed)} 份、"
            f"已是最新 {self.unchanged} 份、失败 {len(self.failures)} 份"
        )


class MirrorProjector:
    """把 DB 行投影成 JSON 镜像，并把镜像对账回 DB。"""

    def __init__(self, storage: ArchiveStorage | None = None) -> None:
        self.storage = storage if storage is not None else get_archive_storage()

    @staticmethod
    def key(asset_id: str) -> str:
        return f"{MIRROR_PREFIX}/{asset_id}.json"

    @staticmethod
    def snapshot(row: Asset) -> MirrorRow:
        """DB 行 → 镜像快照。与业务出参同源，镜像里不会长出第二套字段语义。"""
        payload = ArchiveAssetRepository.to_media(row).model_dump(mode="json", by_alias=True)
        return MirrorRow(
            asset_id=row.id,
            version=int(row.state_version or 0),
            payload=payload,
        )

    def write(self, snapshot: MirrorRow) -> None:
        self.storage.write(self.key(snapshot.asset_id), snapshot.payload)

    def remove(self, asset_id: str) -> None:
        self.storage.remove(self.key(asset_id))

    def _version(self, asset_id: str) -> int | None:
        """镜像当前版本；`None` = 缺失，负数 = 内容损坏（都触发重写）。"""
        key = self.key(asset_id)
        try:
            if not self.storage.exists(key):
                return None
            value = self.storage.read(key)
        except (FileNotFoundError, OSError, ValueError):
            return None
        if not isinstance(value, dict):
            return -1
        raw = value.get("stateVersion", value.get("state_version"))
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return -1

    def reconcile(self, rows: Sequence[MirrorRow], *, execute: bool = True) -> MirrorReport:
        """按 DB 清单收敛镜像：补缺 / 更新落后版本 / 清孤儿。幂等，可重复跑。"""
        report = MirrorReport()
        known = {row.asset_id for row in rows}

        for snapshot in rows:
            if self._version(snapshot.asset_id) == snapshot.version:
                report.unchanged += 1
                continue
            if not execute:
                report.written.append(snapshot.asset_id)
                continue
            try:
                self.write(snapshot)
            except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整轮对账
                report.failures.append((snapshot.asset_id, f"{type(exc).__name__}: {exc}"))
            else:
                report.written.append(snapshot.asset_id)

        for key in self.storage.list_keys(MIRROR_PREFIX, suffix=".json"):
            asset_id = key.rsplit("/", 1)[-1].removesuffix(".json")
            if asset_id in known:
                continue
            if execute:
                try:
                    self.storage.remove(key)
                except Exception as exc:  # noqa: BLE001
                    report.failures.append((asset_id, f"{type(exc).__name__}: {exc}"))
                    continue
            report.removed.append(asset_id)
        return report


async def project_after_commit(row: Asset, storage: ArchiveStorage | None = None) -> bool:
    """事务提交后投影一行；失败只记日志，交由周期对账重放。

    不抛异常是刻意的：DB 已经提交，抛 500 只会让用户以为上传失败而重试，
    而幂等重放本来就能把镜像补回来。
    """
    projector = MirrorProjector(storage)
    snapshot = projector.snapshot(row)
    try:
        await asyncio.to_thread(projector.write, snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "归档镜像投影失败，等待周期对账重放：id=%s version=%s err=%s",
            snapshot.asset_id,
            snapshot.version,
            exc,
        )
        return False
    return True


async def run_mirror_reconcile(
    *, execute: bool = True, session: AsyncSession | None = None
) -> MirrorReport:
    """全量按 DB 重建镜像；`app rebuild-mirrors` 与周期任务共用。"""
    projector = MirrorProjector()
    if session is not None:
        rows = await _snapshots(session)
    else:
        from db.session import get_session_factory

        async with get_session_factory()() as owned:
            rows = await _snapshots(owned)
    return await asyncio.to_thread(projector.reconcile, rows, execute=execute)


async def _snapshots(session: AsyncSession) -> list[MirrorRow]:
    rows = (
        await session.execute(select(Asset).where(Asset.source == AssetSource.ARCHIVE))
    ).scalars().all()
    # 在异步侧就把 ORM 行转成纯数据：SQLAlchemy session 不能跨线程用
    return [MirrorProjector.snapshot(row) for row in rows]
