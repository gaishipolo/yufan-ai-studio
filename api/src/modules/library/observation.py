"""并存观察期的每日对账：只读、只记日志。

## 为什么要有这条周期任务

`app library reconcile` 能给出准确结论，但它要求**有人记得去跑**。观察期通常
持续一两周，中间夹着周末与发布窗口——「昨天补的 3 条今天有没有被新数据盖掉」
这种事，靠人记就会漏。这里每天扫一遍，有缺口时在日志里留一行可搜索的 WARN：

    library.legacy.sync 旧库还有 N 条待补 ...

它**不做任何修复**：补数据要读字节、要写库，属于 `app library import-archive`
的职责，而那条命令必须由人决定什么时候跑（观察期两边都在写入，自动搬运只会
让「谁写的这条」更难查）。

## 为什么没有配置开关

只读、每天两次查询、无副作用。给这种任务加开关的代价是：默认关 → 观察期根本
不会跑（等于没做）；默认开 → 又多一个没人记得的配置项。表不存在时（新部署、
旧模块已下线）静默跳过，也不会产生噪声。
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from core.logging import get_logger
from core.scheduler import periodic
from db.session import get_session_factory
from modules.library.import_archive import GapReport, reconcile_legacy

logger = get_logger(__name__)

__all__ = ["INTERVAL_SEC", "legacy_sync", "run_legacy_sync"]

#: 一天一次。观察期的变化以「天」为单位：人在旧库里删一条、在新库里传一条，
#: 隔天看一次足够；跑得更勤只会把同一条缺口刷很多遍日志。
INTERVAL_SEC = 86_400


async def run_legacy_sync() -> GapReport | None:
    """跑一轮对账并记日志。返回报告（表不存在或读不到时返回 `None`）。"""
    async with get_session_factory()() as session:
        try:
            report = await reconcile_legacy(session)
        except SQLAlchemyError as exc:
            # 旧表不存在是正常状态（新部署 / 旧模块已下线），不是故障。
            logger.info("旧素材表不可读，跳过对账（%s）", type(exc).__name__)
            return None

    if report.in_sync:
        logger.info(
            "对账一致：旧库 %d 条 / v2 已迁 %d 条", report.legacy_total, report.migrated
        )
    else:
        logger.warning("旧库与 v2 有缺口：%s", " | ".join(report.lines(limit=5)))
    return report


@periodic("library.legacy.sync", every=INTERVAL_SEC, leader_only=True)
async def legacy_sync() -> None:
    await run_legacy_sync()
