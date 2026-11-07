"""事件 outbox：可靠投递（表 + 写入 + 投递器）。

## 它补的是哪个洞

`core/events.py` 是**进程内、尽力而为**的总线：订阅者抛异常只记日志，进程崩了事件就没了。
而业务代码的写法是「先 commit 业务行，再 publish」——两次调用之间那个窗口一旦崩掉，
**业务已经成功、事件永久消失**。症状很难查：任务页显示成功，下游通知、审计、扣额度
一个都没发生，日志里也没有任何异常。

outbox 把「要发什么事件」写进**业务同一个事务**：

```python
await record(session, "task.completed", task_id=task.id)   # 与业务行同一次 commit
await session.commit()                                     # 要么都成，要么都不成
```

之后由投递器（本文件的周期任务）把未投递的行发出去。崩溃只会让「投递」晚一点发生，
不会让事件消失。

## 为什么放在 `db/` 而不是 `core/`

outbox 的本质是「**用业务同一个数据库事务**写一行待办」，脱离 SQL 它没有意义；
放在 `core/` 就要让 core 反过来 import `db.session`，而现状是 `db → core`（单向）。
这里和 `db/audit_sink.py` 同一个位置逻辑：core 定义协议与事件名，落库实现放 db 层。
业务侧只需要 `from db.outbox import record`，不需要认识表结构。

## 投递语义：至少一次

`delivered_at` 标记成功；崩溃发生在「已 publish、未标记」之间会导致同一个事件被投两次。
**这是刻意的**，也写在 `core/events.py` 的注释里：订阅方必须幂等（例如按事件 id 去重、
或用 `INSERT ... ON CONFLICT DO NOTHING` 写自己的幂等表）。要做到「恰好一次」需要
分布式事务，本骨架不承诺做不到的事。

「订阅者抛异常」也算一次**失败**（走退避重试），而不是「已投递」——这依赖
`EventBus.publish_strict` 的严格语义。曾经这里写的是 `publish`，而它永不抛，
于是订阅者失败被静默记为成功：事件丢了、`attempts` 永远是 0、`OutboxReport.failed`
永远是 0，整条重试链路在用一份不存在的契约上运行。

## 为什么状态只有三个

`pending → delivered`，或 `pending → failed`（重试用尽）。刻意不做 `delivering`：
那需要租约 + 回收器，而承诺本来就是至少一次。多一个状态只会多一处
「卡在 delivering 永远不动」的故障模式。

## 为什么没有 `max_attempts` 列

重试上限是**部署策略**（`OUTBOX_MAX_ATTEMPTS`），所有事件一样；写进每一行会让
「调大上限」变成一次数据迁移。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from core import trace
from core.config import settings
from core.events import get_event_bus
from core.logging import get_logger
from core.scheduler import periodic
from db.base import Base, UUIDMixin, utcnow
from db.session import get_session_factory

logger = get_logger(__name__)

OUTBOX_PENDING = "pending"
OUTBOX_DELIVERED = "delivered"
OUTBOX_FAILED = "failed"


class OutboxEvent(Base, UUIDMixin):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_ready", "status", "available_at"),)

    # 事件名用 `领域.动作`，与 `core/events.py` 的订阅键一致
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    # 写入时的请求 id（trace）：投递发生在周期任务里，没有它就无法回答
    # 「这条事件是哪个用户动作产生的」。空串 = 非请求触发。
    trace_id: Mapped[str] = mapped_column(
        String(64), default="", server_default="", nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default=OUTBOX_PENDING, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 下次可投递时间：失败后按指数退避推后，别让坏事件每 5 秒重试一次
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# 写入：必须和业务行同一个事务
# --------------------------------------------------------------------------- #
async def record(session: AsyncSession, name: str, /, **payload: Any) -> str:
    """把一条事件登记进 outbox，**不 commit**。

    调用方负责事务边界：与业务写入一起 `commit()`，两者要么都在要么都不在。
    这也是它比 `get_event_bus().publish()` 强的地方——后者永远在事务之外。

    返回事件 id（需要幂等去重时可以用它做业务侧的键）。
    """
    row = OutboxEvent(
        name=name,
        payload=json.dumps(payload, ensure_ascii=False, default=str),
        trace_id=trace.current(),
    )
    session.add(row)
    # flush 只为拿到 id（默认值在 INSERT 时生成）；仍在调用方的事务里
    await session.flush()
    return row.id


# --------------------------------------------------------------------------- #
# 投递
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class OutboxReport:
    delivered: int = 0
    failed: int = 0
    remaining: int = 0

    @property
    def healthy(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        return (
            f"投递 {self.delivered} 条、失败 {self.failed} 条、仍待投递 {self.remaining} 条"
        )


def _decode(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        # 载荷写坏了不能让投递器整批停摆：投一条空载荷出去，订阅方能看出「这条是坏的」
        logger.warning("outbox 载荷不是合法 JSON，按空载荷投递")
        return {}
    return value if isinstance(value, dict) else {"value": value}


# --------------------------------------------------------------------------- #
# 清理
# --------------------------------------------------------------------------- #
async def prune_finished(
    *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> int:
    """删掉投递成功 / 彻底失败且超过保留期的行，返回删除条数。

    没有这一步，一张只增不减的表会在半年后变成运维问题（和 `login_events` 一样）。
    只删终态行：pending 无论多老都要留着——那是还没发出去的事件，删了就是丢数据。
    """
    factory = session_factory or get_session_factory()
    cutoff = utcnow() - timedelta(days=max(1.0, settings.outbox_retention_days))
    async with factory() as session:
        result = await session.execute(
            delete(OutboxEvent)
            .where(
                OutboxEvent.status.in_((OUTBOX_DELIVERED, OUTBOX_FAILED)),
                OutboxEvent.updated_at < cutoff,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    removed = int(getattr(result, "rowcount", 0) or 0)
    if removed:
        logger.info(
            "outbox 清理 %d 条超过 %.0f 天的终态事件", removed, settings.outbox_retention_days
        )
    return removed


@periodic("core.outbox.prune", every=3600.0, immediate=False)
async def prune_loop() -> None:
    """周期入口：`@periodic` 要求 handler 返回 None，清理的真实实现在上面（可单独调）。"""
    await prune_finished()


async def dispatch_pending(
    *,
    limit: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> OutboxReport:
    """把到期的 pending 事件投递出去，返回本轮统计。

    每轮最多 `OUTBOX_BATCH_SIZE` 条：一批太大时，一条慢订阅者会拖住整轮，
    下一轮的 pending 就跟着堆积。宁可多跑几轮。

    投递**不复用同一个事务**（那会把订阅者的耗时锁进数据库事务里，长事务是
    PG 最讨厌的东西）。代价是「已投递但没标成功」时下一轮会重投，即至少一次。
    """
    factory = session_factory or get_session_factory()
    batch = settings.outbox_batch_size if limit is None else limit
    bus = get_event_bus()
    now = utcnow()

    async with factory() as session:
        # 只取列不取 ORM 实例：session 关闭后对象会被 expunge，
        # 拿三列元组去投递，不依赖 lazy load 是否还有效
        rows = (
            await session.execute(
                select(OutboxEvent.id, OutboxEvent.name, OutboxEvent.payload, OutboxEvent.trace_id)
                .where(
                    OutboxEvent.status == OUTBOX_PENDING,
                    OutboxEvent.available_at <= now,
                )
                .order_by(OutboxEvent.created_at)
                .limit(max(1, batch))
            )
        ).all()
        total = int(
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.status == OUTBOX_PENDING)
            )
            or 0
        )

    if not rows:
        return OutboxReport(remaining=total)

    delivered_ids: list[str] = []
    failed: list[tuple[str, str]] = []
    for event_id, name, payload, trace_id in rows:
        # 投递发生在周期任务里：恢复写入时的 trace，订阅方的日志才能接回原始请求
        previous = trace.adopt(trace_id or "")
        try:
            # **必须用 publish_strict**：`publish` 永不抛，订阅者失败会被记成
            # 「投递成功」，重试/退避/`failed` 留档全部变成死代码。
            # 这里要的就是「失败能被看见」，代价是重试会重新执行同一条事件的
            # 其它（本来已经成功的）订阅者——至少一次语义本来就这么要求。
            await bus.publish_strict(name, **_decode(payload))
        except Exception as exc:  # noqa: BLE001 - 单条坏事件不能中断整批
            failed.append((event_id, f"{type(exc).__name__}: {exc}"[:500]))
        else:
            delivered_ids.append(event_id)
        finally:
            trace.release(previous)

    # 成功的一批合并成一条 UPDATE；失败的逐条记原因与退避
    async with factory() as session:
        if delivered_ids:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id.in_(delivered_ids))
                # 关掉 ORM 的内存求值：sqlite 读回来是 naive datetime，
                # Python 侧拿它和 aware 的 utcnow 比较会直接 TypeError
                .execution_options(synchronize_session=False)
                .values(status=OUTBOX_DELIVERED, delivered_at=utcnow(), updated_at=utcnow())
            )
        await session.commit()

    for event_id, error in failed:
        await _defer(event_id, error, session_factory=factory)

    return OutboxReport(
        delivered=len(delivered_ids),
        failed=len(failed),
        remaining=max(0, total - len(delivered_ids)),
    )


async def _defer(
    event_id: str,
    error: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """记一次失败并推后重试；到顶转 failed 留档。"""
    async with session_factory() as session:
        row = await session.get(OutboxEvent, event_id)
        if row is None:
            return
        attempts = row.attempts + 1
        values: dict[str, Any] = {
            "attempts": attempts,
            "last_error": error,
            "updated_at": utcnow(),
        }
        if attempts >= max(1, settings.outbox_max_attempts):
            values["status"] = OUTBOX_FAILED
        else:
            # 指数退避：5s、10s、20s… 上限 10 分钟
            backoff = min(600.0, settings.outbox_dispatch_interval_sec * (2**attempts))
            values["available_at"] = utcnow() + timedelta(seconds=backoff)
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .execution_options(synchronize_session=False)
            .values(**values)
        )
        await session.commit()
    logger.warning("outbox 投递失败 id=%s err=%s", event_id, error)


# --------------------------------------------------------------------------- #
# 周期投递
# --------------------------------------------------------------------------- #
@periodic(
    "core.outbox",
    every=max(1.0, settings.outbox_dispatch_interval_sec),
    immediate=False,
)
async def dispatch_loop() -> None:
    """框架自带的周期任务：名字用 `core.` 前缀，表示不归属任何业务模块。

    不用 `leader_only`：多实例各跑一份只会造成重复投递（至少一次语义允许），
    而抢租约反而会引入「租约持有者挂了 → 这一轮没人投递」的额外故障模式。
    """
    if not settings.outbox_enabled:
        return
    report = await dispatch_pending()
    if report.delivered or report.failed:
        logger.info("outbox：%s", report.summary())


__all__ = [
    "OUTBOX_DELIVERED",
    "OUTBOX_FAILED",
    "OUTBOX_PENDING",
    "OutboxEvent",
    "OutboxReport",
    "dispatch_loop",
    "dispatch_pending",
    "prune_finished",
    "prune_loop",
    "record",
]
