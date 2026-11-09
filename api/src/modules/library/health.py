"""资产库 v2 的部署自检：`ModuleSpec.doctor` 指向这里。

三条检查，各自对应一种「不报错但会慢慢坏」的状态：

- `library.ports`：注入口有没有接上。`library-ingest` 要到 M3 才接，所以它在
  未接上时只记 INFO；`library-lookup` / `library-references` 在 M2 已接地，
  缺任何一个都是装配故障（引用检查缺失会让「彻底删除」一律 503）。
- `library.trash`：回收站里**已过期但仍未清理**的条数。GC 默认关闭，所以这项
  平时是 INFO；一旦条数持续增长且 GC 开着，说明有引用一直挡着删除。
- `library.share_requests`：pending 超过 7 天的申请单。审批流最典型的死法是
  「没人知道有这张单」——资产既删不掉也共享不出去，而日志里一句错都没有。

纪律与其它模块自检一致：只往报告里加自己那条，不改别人的结论。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from api.module import DoctorReportLike, Level
from core.config import settings
from core.ports.library_ingest import has_library_ingest
from core.ports.library_lookup import has_library_lookup
from core.ports.library_references import has_library_references
from db.session import get_session_factory
from modules.library.models import LibraryAsset, LibraryShareRequest

__all__ = ["check"]

_STALE_REQUEST_DAYS = 7


async def check(report: DoctorReportLike) -> None:
    required_missing = [
        name
        for name, connected in (
            ("library-lookup", has_library_lookup()),
            ("library-references", has_library_references()),
        )
        if not connected
    ]
    if required_missing:
        report.add(
            "library.ports",
            "FAIL",
            f"注入口未接上：{', '.join(required_missing)}"
            "（引用检查缺失会让彻底删除一律 503，这是刻意的 fail-closed）",
        )
    elif not has_library_ingest():
        report.add(
            "library.ports", "INFO", "library-ingest 未接上（M3 接入生成入库后才需要）"
        )
    else:
        report.add("library.ports", "PASS", "三个注入口均已接上")

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
