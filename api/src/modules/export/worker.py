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

import asyncio
from datetime import timedelta

from core.config import get_settings
from core.exceptions import BadRequest, Forbidden, UpstreamError
from core.logging import get_logger
from core.metrics import METRICS
from db.base import utcnow
from db.repository import BaseRepository
from db.session import get_session_factory
from infra import get_storage
from infra.queue.base import Job
from modules.export.enums import ACTIVE_STATUSES, ExportStatus
from modules.export.errors import ExportTooLarge
from modules.export.models import Export
from modules.export.sources import lookup_source, render_csv
from workers import register_handler

logger = get_logger(__name__)

#: 异常类型 → 面向用户的中文原因。
#:
#: 对外字段（`ExportOut.error`）**不能写原始异常**：`str(exc)` 里可能带内部文件
#: 路径、SQL 片段、数据库连接串、上游依赖地址，等于给前端开了一条信息泄露通道；
#: 同一模块的其它两处错误都写的用户文案（`service.py` 的「任务入队失败，请重新
#: 导出」「导出超时未完成，请重新导出」），只有 worker 这一处是原始异常——口径不一
#: 致。完整 traceback 只进日志（`logger.exception`），对外只留一句看得懂的话。
_ERROR_HINTS: tuple[tuple[type[Exception], str], ...] = (
    (Forbidden, "没有导出该数据的权限，请联系管理员"),
    (BadRequest, "导出参数不合法，请调整后重试"),
    (UpstreamError, "上游服务异常，请稍后重试"),
    (TimeoutError, "数据源查询超时，请缩小筛选范围后重试"),
    (ExportTooLarge, "导出内容过大，请缩小筛选范围后重试"),
)

#: 兜底文案：不能出现异常类型名、路径或连接串
_GENERIC_ERROR = "导出失败，请稍后重试；若持续失败请联系管理员"


def _user_error(exc: Exception) -> str:
    """把异常翻译成一句可以给用户看的话。"""
    for exc_type, message in _ERROR_HINTS:
        if isinstance(exc, exc_type):
            return message
    return _GENERIC_ERROR

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
            payload = await asyncio.to_thread(
                render_csv,
                source.columns,
                rows,
                max_bytes=settings.export_max_bytes,
            )

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
            # 但仍然要把原因留给用户——只是给**用户文案**，不是原始异常；
            # 完整 traceback 留给下面的 logger.exception。
            record.status = ExportStatus.FAILED.value
            record.error = _user_error(exc)
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
