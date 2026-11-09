"""资产库 v2 的部署自检：`ModuleSpec.doctor` 指向这里。

四条检查，各自对应一种「不报错但会慢慢坏」的状态：

- `library.ports`：四个注入口有没有接上。任何一个缺失都是装配故障——
  读查询缺了选择器拿不到资产，引用检查缺了「彻底删除」一律 503，
  入库缺了生成结果会丢（fail-closed 会让生成侧报错，还算看得见）。
- `library.trash`：回收站里**已过期但仍未清理**的条数。GC 默认关闭，所以这项
  平时是 INFO；一旦条数持续增长且 GC 开着，说明有引用一直挡着删除。
- `library.share_requests`：pending 超过 7 天的申请单。审批流最典型的死法是
  「没人知道有这张单」——资产既删不掉也共享不出去，而日志里一句错都没有。
- `library.downloads`：卡住的打包任务与没清掉的过期 zip。打包任务卡在
  `running` 时前端会永远转圈；而「过期却没清」只占磁盘，不表现成任何错误。
- `library.legacy_sync`：并存观察期的对账。旧 `assets` 表与 v2 的 `legacy:<id>`
  行有没有缺口。观察期两边都会有人写，「新库看起来正常」并不等于「旧库的增量
  都追平了」——这一条就是把 `app library reconcile` 的结论摆到部署自检里。

纪律与其它模块自检一致：只往报告里加自己那条，不改别人的结论。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from api.module import DoctorReportLike, Level
from core.config import settings
from core.ports.library_ingest import has_library_ingest
from core.ports.library_lookup import has_library_lookup
from core.ports.library_references import has_library_references
from db.session import get_session_factory
from modules.library.downloads import QUEUED, RUNNING, STUCK_AFTER, SUCCEEDED
from modules.library.import_archive import reconcile_legacy
from modules.library.models import (
    LibraryAsset,
    LibraryDownloadJob,
    LibraryShareRequest,
)

__all__ = ["check"]

_STALE_REQUEST_DAYS = 7


async def check(report: DoctorReportLike) -> None:
    missing = [
        name
        for name, connected in (
            ("library-lookup", has_library_lookup()),
            ("library-ingest", has_library_ingest()),
            ("library-references", has_library_references()),
        )
        if not connected
    ]
    if missing:
        report.add(
            "library.ports",
            "FAIL",
            f"注入口未接上：{', '.join(missing)}"
            "（引用检查缺失会让彻底删除一律 503，入库缺失会让生成结果无处可写）",
        )
    else:
        report.add("library.ports", "PASS", "读 / 写 / 引用三个注入口均已接上")

    async with get_session_factory()() as session:
        now = datetime.now(UTC)
        expired = int(
            await session.scalar(
                select(func.count())
                .select_from(LibraryAsset)
                .where(
                    LibraryAsset.state == "trash",
                    LibraryAsset.trash_expires_at.is_not(None),
                    LibraryAsset.trash_expires_at <= now,
                )
            )
            or 0
        )
        if expired == 0:
            report.add("library.trash", "PASS", "没有过期待清理的回收站条目")
        else:
            level: Level = (
                "INFO" if not settings.library_trash_gc_enabled else "WARN"
            )
            report.add(
                "library.trash",
                level,
                f"{expired} 条回收站条目已过保留期未清理"
                + (
                    "（LIBRARY_TRASH_GC_ENABLED=false，属预期）"
                    if not settings.library_trash_gc_enabled
                    else "（GC 已开但仍未清掉，多半是被封装/下载任务引用）"
                ),
            )

        stale_before = now - timedelta(days=_STALE_REQUEST_DAYS)
        stale = int(
            await session.scalar(
                select(func.count())
                .select_from(LibraryShareRequest)
                .where(
                    LibraryShareRequest.status == "pending",
                    LibraryShareRequest.created_at <= stale_before,
                )
            )
            or 0
        )
        if stale:
            report.add(
                "library.share_requests",
                "WARN",
                f"{stale} 条共享/撤销申请待审超过 {_STALE_REQUEST_DAYS} 天"
                "：资产既共享不出去也删不掉，需要有人处理或改配组长",
            )
        else:
            report.add("library.share_requests", "PASS", "没有长期待审的申请单")

        stuck = int(
            await session.scalar(
                select(func.count())
                .select_from(LibraryDownloadJob)
                .where(
                    LibraryDownloadJob.status.in_((QUEUED, RUNNING)),
                    LibraryDownloadJob.created_at <= now - STUCK_AFTER,
                )
            )
            or 0
        )
        overdue = int(
            await session.scalar(
                select(func.count())
                .select_from(LibraryDownloadJob)
                .where(
                    LibraryDownloadJob.status == SUCCEEDED,
                    LibraryDownloadJob.expires_at.is_not(None),
                    LibraryDownloadJob.expires_at <= now,
                )
            )
            or 0
        )
        if stuck:
            report.add(
                "library.downloads",
                "WARN",
                f"{stuck} 条打包任务卡在排队/执行超过 {STUCK_AFTER.total_seconds() / 3600:.0f} 小时"
                "：前端会一直转圈，检查 worker 是否在跑，或用 `app library download-run` 补跑",
            )
        elif overdue and not settings.library_download_gc_enabled:
            report.add(
                "library.downloads",
                "INFO",
                f"{overdue} 个过期 zip 未清理（LIBRARY_DOWNLOAD_GC_ENABLED=false，属预期）",
            )
        elif overdue:
            report.add(
                "library.downloads",
                "WARN",
                f"{overdue} 个过期 zip 未清理（GC 已开，检查 library.download.gc 是否在跑）",
            )
        else:
            report.add("library.downloads", "PASS", "没有卡住的打包任务与过期 zip")

        try:
            gap = await reconcile_legacy(session)
        except SQLAlchemyError as exc:
            # 旧表不存在是**正常状态**（新部署、或旧模块已彻底下线），
            # 不该让 v2 的自检变红——它不是 v2 的部署条件。
            report.add(
                "library.legacy_sync",
                "INFO",
                f"旧素材表不可读，跳过对账（{type(exc).__name__}）",
            )
        else:
            if gap.legacy_total == 0 and not gap.orphan:
                report.add("library.legacy_sync", "INFO", "旧素材库没有数据，无需对账")
            elif gap.in_sync:
                report.add(
                    "library.legacy_sync",
                    "PASS",
                    f"{gap.legacy_total} 条旧素材全部已迁",
                )
            else:
                report.add(
                    "library.legacy_sync",
                    "WARN",
                    f"旧库还有 {len(gap.missing_supported)} 条待补 / "
                    f"{len(gap.missing_unsupported)} 条格式不支持 / "
                    f"{len(gap.orphan)} 条 v2 独有"
                    "：跑 `app library reconcile` 看明细，"
                    "`app library import-archive` 追平可补的那部分",
                )
