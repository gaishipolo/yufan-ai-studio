"""导出领域服务：业务规则都在这里，路由只做协议转换。

约定：一个 service 方法 = 一次事务提交（`await self.session.commit()`）。

## 开箱可用的横向能力（直接 import，不要自己 new）

| 能力 | 入口 | 注意 |
|---|---|---|
| 缓存 | `from infra import get_cache` | 只放可丢失的数据（限流计数、热点只读） |
| 出站 HTTP | `from infra.http import get_http, fetch_bytes` | 过 SSRF 校验、限大小、只重试幂等码 |
| 队列 | `from infra import get_queue` + `Job(name=..., payload=...)` | 只拿协议，换驱动业务不改 |
| 可靠事件 | `from db.outbox import record` | **与业务行同一次 commit**，不要单独提交 |
| 限流 | 路由上 `Depends(rate_limit("export.create", limit=30))` | 只在显式挂载的接口生效 |
| 功能开关 | `from core.flags import is_on` | 名字用 `领域.动作`，doctor 会列出全部 |
| 指标 | `from core.metrics import METRICS` | 导入期声明一次；标签只用低基数字段 |
| 上下文 | `from core.context import get_current_user, get_request_id` | 日志已自动带 request id |

## 这个模块特有的三处坑

1. **入队必须在 `commit()` 之后**。先入队后提交时，worker 可能在行还没落库的
   瞬间取到 job，查不到记录就直接丢掉——症状是「有时候导出永远 queued」。
2. **判重不要用 SQL 比较 JSON 列**。sqlite 把 JSON 存成 TEXT，`params = :p` 比的是
   文本，键顺序一变就不相等；postgres 的 `jsonb` 又按结构比。同一个判断在两种
   库上结果不同，只能把「进行中的那几行」拉回来在 Python 里比。
3. **下载只认 `object_key`，不认 `status`**。产物可能被运维手动删掉，
   行还停在 `succeeded`——`local_path()` 拿到路径后要再 `exists()` 一次。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.config import get_settings
from core.context import CurrentUser
from core.exceptions import NotFound
from core.logging import get_logger
from core.response import Page, PageParams
from core.serving import Served
from db.base import utcnow
from db.repository import BaseRepository
from infra import get_queue, get_storage
from infra.queue.base import Job
from modules.export.enums import ACTIVE_STATUSES, ExportStatus
from modules.export.errors import ExportBusy, ExportGone, ExportNotReady
from modules.export.models import Export
from modules.export.schema import CreateExportIn, ExportDetailOut, ExportOut
from modules.export.sources import normalize_params, resolve_source

logger = get_logger(__name__)

#: 单次 job 的上限（秒）。必须小于 `export_stale_after_sec`，
#: 否则「还在跑」会被周期任务判成「卡死」。
BUILD_TIMEOUT_SEC = 600.0




class ExportService:
    def __init__(self, session: AsyncSession, repo: BaseRepository[Export]) -> None:
        self.session = session
        self.repo = repo
        self.settings = get_settings()

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    async def list_items(
        self,
        actor: CurrentUser,
        page: PageParams,
        *,
        status: str | None = None,
        data_source: str | None = None,
    ) -> Page[ExportOut]:
        stmt = select(Export).where(Export.requested_by == actor.id)
        if status:
            stmt = stmt.where(Export.status == status)
        if data_source:
            stmt = stmt.where(Export.data_source == data_source)
        # 排序键以主键收尾：同一毫秒的两行在翻页边界上顺序不定
        stmt = stmt.order_by(Export.created_at.desc(), Export.id.desc())

        rows, total = await self.repo.paginate(stmt, page)
        return Page.create([ExportOut.model_validate(row) for row in rows], total, page)

    async def detail(self, actor: CurrentUser, export_id: str) -> ExportDetailOut:
        record = await self._get_owned(actor, export_id)
        out = ExportDetailOut.model_validate(record)
        out.downloadable = record.status == ExportStatus.SUCCEEDED.value and bool(
            record.object_key
        )
        return out

    async def download(self, actor: CurrentUser, export_id: str) -> Served:
        record = await self._get_owned(actor, export_id)
        if record.status == ExportStatus.EXPIRED.value:
            raise ExportGone()
        if record.status != ExportStatus.SUCCEEDED.value or not record.object_key:
            raise ExportNotReady()

        key = record.object_key
        storage = get_storage()
        path = storage.local_path(key)
        if path is not None and not path.exists():
            # 行说成功了、文件没了：运维清过目录，或换了驱动没迁移。
            # 告诉用户「重新导一次」，不要 500。
            logger.warning("导出产物丢失 id=%s key=%s", record.id, key)
            raise ExportGone()

        filename = f"{record.data_source}-{record.id}.csv"
        await audit.record(
            "export.download",
            actor_id=actor.id,
            target_type="export",
            target_id=record.id,
            detail={"data_source": record.data_source, "row_count": record.row_count},
        )
        if path is not None:
            return Served(path=path, mime="text/csv", filename=filename)
        url = await storage.presigned_download_url(key, expires_in=300)
        return Served(url=url, mime="text/csv")

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    async def create(self, actor: CurrentUser, payload: CreateExportIn) -> ExportOut:
        # 字段注解是 `ExportSource`，运行时就是枚举实例——`.value` 有类型检查兜底，
        # `str()` 只是把「本该被 mypy 抓到的拼写错误」拖到运行期才发现。
        source_name = payload.data_source.value
        # 权限与「源是否存在」一起判：不存在是 400（客户端传错），没权限是 403
        resolve_source(source_name, actor)
        params = normalize_params(payload.params.model_dump(exclude_none=True))

        await self._reject_duplicate(actor.id, source_name, params)

        record = Export(
            data_source=source_name,
            params=params,
            status=ExportStatus.QUEUED.value,
            requested_by=actor.id,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)

        try:
            await get_queue().enqueue(
                Job(
                    name="export.build",
                    payload={"id": record.id},
                    max_attempts=1,
                    idempotency_key=f"export:{record.id}",
                    timeout_sec=BUILD_TIMEOUT_SEC,
                )
            )
        except Exception as exc:  # noqa: BLE001
            # 行已落库但活没排上：不置失败的话它会永远 queued，
            # 用户看到「排队中」直到天荒地老，而日志里什么都没有。
            record.status = ExportStatus.FAILED.value
            record.error = "任务入队失败，请重新导出"
            await self.session.commit()
            logger.exception("导出入队失败 id=%s", record.id)
            raise ExportBusy("导出服务暂时不可用，请稍后重试") from exc

        await audit.record(
            "export.create",
            actor_id=actor.id,
            target_type="export",
            target_id=record.id,
            detail={"data_source": source_name, "params": params},
        )
        logger.info(
            "导出已受理 id=%s source=%s actor=%s", record.id, source_name, actor.id
        )
        return ExportOut.model_validate(record)

    async def delete(self, actor: CurrentUser, export_id: str) -> None:
        record = await self._get_owned(actor, export_id)
        if record.status == ExportStatus.RUNNING.value:
            # 正在写 CSV：删了会让 worker 的 UPDATE 打到不存在的行上
            raise ExportBusy("导出正在生成，请等它完成后再删除")

        object_key = record.object_key
        source = record.data_source
        if object_key:
            try:
                await get_storage().delete(object_key)
            except Exception:  # noqa: BLE001
                # 字节删不掉不该阻止删记录：留下的是孤儿文件，
                # 比「用户永远删不掉一条记录」好
                logger.warning("导出产物删除失败 key=%s", object_key)

        await self.repo.delete(record)
        await self.session.commit()
        await audit.record(
            "export.delete",
            actor_id=actor.id,
            target_type="export",
            target_id=export_id,
            detail={"data_source": source},
        )

    # ------------------------------------------------------------------ #
    # 运维（周期任务调用）
    # ------------------------------------------------------------------ #
    async def reclaim_expired(self, *, limit: int = 200) -> dict[str, int]:
        """回收到期产物：删字节、状态转 expired、记录保留。"""
        now = utcnow()
        stmt = (
            select(Export)
            .where(
                Export.status == ExportStatus.SUCCEEDED.value,
                Export.expires_at.is_not(None),
                Export.expires_at < now,
            )
            .order_by(Export.expires_at.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        reclaimed = 0
        failed = 0
        for row in rows:
            if row.object_key:
                try:
                    await get_storage().delete(row.object_key)
                except Exception:  # noqa: BLE001
                    # 删不掉就下轮再试：状态不改，下一条继续
                    failed += 1
                    logger.warning("回收导出产物失败 id=%s", row.id)
                    continue
            row.status = ExportStatus.EXPIRED.value
            row.object_key = None
            reclaimed += 1

        if reclaimed or failed:
            await self.session.commit()
        return {"scanned": len(rows), "reclaimed": reclaimed, "failed": failed}

    async def fail_stale(self, *, older_than_sec: int) -> int:
        """把「卡住」的导出置失败。

        进程内队列重启即丢，`queued` / `running` 会永远挂在列表上转圈。
        这条兜底让用户至少能看到一个明确的失败，而不是无限等待。
        """
        cutoff = utcnow() - timedelta(seconds=older_than_sec)
        stmt = select(Export).where(
            Export.status.in_(ACTIVE_STATUSES),
            Export.created_at < cutoff,
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        for row in rows:
            row.status = ExportStatus.FAILED.value
            row.error = "导出超时未完成，请重新导出"
        if rows:
            await self.session.commit()
            logger.warning("有 %d 条导出超时被置失败", len(rows))
        return len(rows)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    async def _get_owned(self, actor: CurrentUser, export_id: str) -> Export:
        record = await self.repo.get(export_id)
        # 别人的导出一律 404，不用 403：403 等于告诉对方「这条 id 是真的」
        if record is None or record.requested_by != actor.id:
            raise NotFound("导出记录不存在")
        return record

    async def _reject_duplicate(
        self, actor_id: str, source_name: str, params: dict[str, Any]
    ) -> None:
        """同一个用户对同一份数据只允许一份在跑。

        为什么把「进行中的那几行」拉回 Python 比：SQL 里 `params = :p` 在
        sqlite（TEXT 比较，键序敏感）和 postgres（jsonb 结构比较）结果不一致，
        同一个判断在两种库上会给不同答案。进行中的行最多几条，成本可以忽略。
        """
        stmt = select(Export).where(
            Export.requested_by == actor_id,
            Export.data_source == source_name,
            Export.status.in_(ACTIVE_STATUSES),
        )
        active = (await self.session.execute(stmt)).scalars().all()
        for row in active:
            if dict(row.params or {}) == params:
                raise ExportBusy()


__all__ = ["BUILD_TIMEOUT_SEC", "ExportService"]
