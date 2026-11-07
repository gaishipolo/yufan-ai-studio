"""就绪判定：把「依赖还能不能用」变成结构化结论。

## 为什么放在健康模块里

它要同时问数据库、队列、缓存、outbox 四个地方，`core` 不能做（会让
`core → db/infra` 出现反向依赖），`api/` 也不能做（业务模块只允许依赖
`api.module` / `api.deps` / `api.authn` / `api.accounts`，引用 `api.health`
会被架构守卫拦下）。健康模块本身就是系统模块，它 import `db` / `infra` / `core`
是允许的方向。

## 三种结论，语义必须分清

| 结论 | 含义 | 调用方该做什么 |
|---|---|---|
| `ok` | 全部依赖可用 | 正常接流量 |
| `degraded` | 服务能跑，但有东西在漏（outbox 堆积、死信、缓存不可用） | 继续接流量，同时**告警** |
| `down` | 数据库或队列不可用 | 摘掉这台实例 |

关键：`degraded` **不是** `down`。把 outbox 积压报成 down，会让编排系统把整批
实例一起摘掉——本来只是「事件晚投递几分钟」，变成「服务全挂」。反过来，把数据库
不可用报成 degraded，则会出现「探针全绿、用户全是 500」。分界线是「还能不能正确
服务请求」，不是「有没有问题」。

## 只读、快、不抛

探针每几秒被调一次，**不能**在里面跑 `doctor` 那种全表扫描或 S3 连通性测试：
- 慢探针会被编排系统判超时，表现和「服务挂了」完全一样；
- 写探针（除缓存那个往返）会污染业务数据。

唯一的写操作是缓存 `set → get → delete`：不写就读不出「配了远端缓存但连不上」
这个故障，而它恰好是限流与热点的单点依赖。键固定、TTL 极短，不会积累。
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)

_CACHE_PROBE_KEY = "health:ready"


@dataclass(frozen=True, slots=True)
class RuntimeCheck:
    name: str
    ok: bool
    detail: str
    # 必需项不可用时整体判 down；非必需项只把状态降到 degraded
    required: bool = True


@dataclass
class RuntimeStatus:
    checks: list[RuntimeCheck] = field(default_factory=list)
    database: str = "ok"
    queue_size: int = 0

    @property
    def down(self) -> bool:
        return any(not check.ok and check.required for check in self.checks)

    @property
    def degraded(self) -> bool:
        return any(not check.ok for check in self.checks)

    @property
    def status(self) -> str:
        if self.down:
            return "down"
        return "degraded" if self.degraded else "ok"

    def failures(self) -> list[RuntimeCheck]:
        return [check for check in self.checks if not check.ok]


async def collect_runtime_status(session: AsyncSession) -> RuntimeStatus:
    status = RuntimeStatus()
    status.checks.append(await _check_database(session))
    status.database = "ok" if status.checks[-1].ok else "down"

    queue_size, queue_check = await _check_queue()
    status.queue_size = queue_size
    status.checks.append(queue_check)

    # 数据库都不可用时不再去查 outbox：多一个必然失败的超时只会拖慢探针
    if status.database == "ok":
        status.checks.extend(await _check_outbox(session))
        status.checks.extend(await _check_dead_jobs(session))
    status.checks.append(await _check_cache())
    return status


async def _check_database(session: AsyncSession) -> RuntimeCheck:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 探针不抛
        with suppress(Exception):
            await session.rollback()
        return RuntimeCheck("database", False, f"{type(exc).__name__}: {exc}")
    return RuntimeCheck("database", True, "SELECT 1 成功")


async def _check_queue() -> tuple[int, RuntimeCheck]:
    from infra import get_queue

    try:
        queue = get_queue()
        stats = getattr(queue, "stats", None)
        if callable(stats):
            counts: dict[str, int] = await stats()
            size = sum(counts.values())
            detail = "、".join(f"{name}={count}" for name, count in sorted(counts.items()))
            return size, RuntimeCheck("queue", True, detail)
        size = await queue.size()
        return size, RuntimeCheck("queue", True, f"在途 {size}")
    except Exception as exc:  # noqa: BLE001
        # 队列不可用 = 任务永远不执行，对用户来说和「服务挂了」等价
        return -1, RuntimeCheck("queue", False, f"{type(exc).__name__}: {exc}")


async def _check_outbox(session: AsyncSession) -> list[RuntimeCheck]:
    from db.outbox import OUTBOX_FAILED, OUTBOX_PENDING, OutboxEvent

    try:
        rows = await session.execute(
            select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)
        )
        counts = {str(name): int(count) for name, count in rows.all()}
    except Exception as exc:  # noqa: BLE001 - 首启还没建表
        logger.debug("outbox 就绪检查失败：%s", exc)
        return []

    pending = counts.get(OUTBOX_PENDING, 0)
    failed = counts.get(OUTBOX_FAILED, 0)
    checks = [
        RuntimeCheck(
            "outbox.pending",
            pending <= settings.health_outbox_pending_limit,
            f"待投递 {pending}（阈值 {settings.health_outbox_pending_limit}）",
            required=False,
        )
    ]
    if failed:
        checks.append(
            RuntimeCheck(
                "outbox.failed",
                False,
                f"{failed} 条事件重试用尽，需人工处理",
                required=False,
            )
        )
    return checks


async def _check_dead_jobs(session: AsyncSession) -> list[RuntimeCheck]:
    from db.jobs import JOB_DEAD, QueuedJob

    try:
        dead = int(
            await session.scalar(
                select(func.count()).select_from(QueuedJob).where(QueuedJob.status == JOB_DEAD)
            )
            or 0
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("死信就绪检查失败：%s", exc)
        return []
    return [
        RuntimeCheck(
            "queue.dead",
            dead <= settings.health_dead_job_limit,
            f"死信 {dead}（阈值 {settings.health_dead_job_limit}）",
            required=False,
        )
    ]


async def _check_cache() -> RuntimeCheck:
    from infra import get_cache

    try:
        cache = get_cache()
        await cache.set(_CACHE_PROBE_KEY, b"1", ttl=2)
        value = await cache.get(_CACHE_PROBE_KEY)
        with suppress(Exception):
            await cache.delete(_CACHE_PROBE_KEY)
    except Exception as exc:  # noqa: BLE001
        # 缓存挂了不影响正确性（限流降级放行、热点回源），但吞吐会掉，属于 degraded
        return RuntimeCheck("cache", False, f"{type(exc).__name__}: {exc}", required=False)
    if value != b"1":
        return RuntimeCheck("cache", False, "写入后读不回来（驱动实现有问题）", required=False)
    return RuntimeCheck("cache", True, "读写往返正常", required=False)


__all__ = ["RuntimeCheck", "RuntimeStatus", "collect_runtime_status"]
