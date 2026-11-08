"""导出任务处理器：把一条 queued 记录变成一份 CSV。

## 状态机

    queued ──(领取)──> running ──(写盘成功)──> succeeded ──(到期)──> expired
                          └──(异常)──> failed

## 三条约定

1. **幂等**：不是 `queued`/`running` 的记录直接返回。进程内队列重启会丢 job，
   但 db 驱动的 job 可能被重投；重复执行时第二遍必须什么都不做。
2. **失败不重试**。`max_attempts=1` + 这里把异常吞掉置 `failed`：导出是用户
   主动点的操作，失败了让他再点一次，比在数据源故障时反复打库更合理。
   真要重试，用户点第二次会新建一条（参数判重只在 `queued`/`running` 时生效）。
3. **行数上限写进 SQL**，不是 `rows[:limit]`。数据源自己负责 `LIMIT` 下推，
   否则「限 10 万行」等于先把 500 万行读进内存再切。
"""

from __future__ import annotations

from datetime import timedelta

from core.config import get_settings
from core.logging import get_logger
from core.metrics import METRICS
from db.base import utcnow
from db.repository import BaseRepository
from db.session import get_session_factory
from infra import get_storage
from infra.queue.base import Job
from modules.export.enums import ACTIVE_STATUSES, ExportStatus
from modules.export.models import Export
from modules.export.sources import lookup_source, render_csv
from workers import register_handler

logger = get_logger(__name__)

# 导入期声明一次。标签只用低基数字段：outcome 是枚举，data_source 是有限集合。
BUILD_TOTAL = METRICS.counter(
    "export_build_total", "导出生成次数", ("data_source", "outcome")
)
BUILD_ROWS = METRICS.histogram(
    "export_build_rows", "单次导出的行数分布", buckets=(100, 1000, 10_000, 50_000, 100_000)
)


def _object_key(export_id: str) -> str:
    """按年月分区：运维清理时能按目录成片删，不必逐 key 查。"""
    now = utcnow()
    return f"exports/{now:%Y}/{now:%m}/{export_id}.csv"


@register_handler("export.build")
async def build_export(job: Job) -> None:
    export_id = str(job.payload["id"])
    settings = get_settings()

    async with get_session_factory()() as session:
        repo = BaseRepository(session, Export)
        record = await repo.get(export_id)
        if record is None:
            # 用户建完就删了。不是错误，记一条 info 即可。
            logger.info("导出记录已不存在，跳过 id=%s", export_id)
            return
        if record.status not in ACTIVE_STATUSES:
            logger.info(
                "导出已是终态，跳过 id=%s status=%s", export_id, record.status
            )
            return

        source_name = record.data_source
        record.status = ExportStatus.RUNNING.value
        record.started_at = utcnow()
        await session.commit()

        try:
            source = lookup_source(source_name)
            rows = await source.fetch(
                session,
                actor_id=record.requested_by,
                params=dict(record.params or {}),
                limit=settings.export_max_rows,
            )
            payload = render_csv(source.columns, rows)

            key = _object_key(record.id)
            await get_storage().put(key, payload, "text/csv")

            record.object_key = key
            record.row_count = len(rows)
            record.byte_size = len(payload)
            record.status = ExportStatus.SUCCEEDED.value
            record.error = None
            record.finished_at = utcnow()
            record.expires_at = utcnow() + timedelta(hours=settings.export_ttl_hours)
            await session.commit()
            # session 关闭后 ORM 属性会过期，块外再读会 DetachedInstanceError
            row_count = record.row_count
            byte_size = record.byte_size
        except Exception as exc:  # noqa: BLE001
            # 这里吞掉异常是刻意的：见模块 docstring 第 2 条。
            # 但仍然要把原因留给用户，并把 attempts 加一。
            record.status = ExportStatus.FAILED.value
            record.error = str(exc)[:512] or type(exc).__name__
            record.attempts += 1
            record.finished_at = utcnow()
            await session.commit()
            logger.exception("导出生成失败 id=%s source=%s", export_id, source_name)
            BUILD_TOTAL.inc(data_source=source_name, outcome="failed")
            return

    BUILD_TOTAL.inc(data_source=source_name, outcome="succeeded")
    BUILD_ROWS.observe(float(row_count))
    logger.info(
        "导出完成 id=%s source=%s rows=%d bytes=%d",
        export_id,
        source_name,
        row_count,
        byte_size,
    )
