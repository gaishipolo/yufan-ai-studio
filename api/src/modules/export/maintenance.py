"""导出的周期维护：回收过期产物、兜底卡住的记录。

两个任务都**幂等**，多副本各跑一份也不会出错。区别在 `leader_only`：

- 回收要删存储里的字节，是不可逆的。多实例同时扫到同一行时，删两次虽然是
  空操作，但换成对象存储后每次 delete 都是计费请求，没必要；标 `leader_only`
  让整个集群只跑一次。
- 置失败只是 UPDATE，幂等，不必抢主——抢主反而会在「原主实例挂了」时留下
  一段没人兜底的窗口。
"""

from __future__ import annotations

from core.config import get_settings
from core.logging import get_logger
from core.scheduler import periodic
from db.repository import BaseRepository
from db.session import get_session_factory
from modules.export.models import Export
from modules.export.service import ExportService

logger = get_logger(__name__)


@periodic("export.reclaim", every=3600, leader_only=True)
async def reclaim_expired() -> None:
    async with get_session_factory()() as session:
        service = ExportService(
            session=session, repo=BaseRepository(session, Export)
        )
        stats = await service.reclaim_expired()
    if stats["scanned"]:
        logger.info(
            "导出回收：扫描 %d、回收 %d、失败 %d",
            stats["scanned"],
            stats["reclaimed"],
            stats["failed"],
        )


@periodic("export.stale", every=600, immediate=True)
async def fail_stale() -> None:
    settings = get_settings()
    async with get_session_factory()() as session:
        service = ExportService(
            session=session, repo=BaseRepository(session, Export)
        )
        count = await service.fail_stale(
            older_than_sec=settings.export_stale_after_sec
        )
    if count:
        logger.warning("导出超时置失败：%d 条", count)
