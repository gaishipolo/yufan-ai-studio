"""周期任务扩展点（扩展点第 4 维）。

## 为什么需要它

生成框架解决了「提交 → 轮询 → 落结果」这条**有终点的**链路，但没有解决
「没人触发却必须定期发生」的事：

- 进程内队列重启即丢，`queued` 的记录会永远挂在页面上（生图就撞上了这个）；
- 上传到共享盘后没人用的孤儿文件，要按天数清理；
- 「每天凌晨重置某些计数」这类运营动作。

这些都不属于任何一次请求，也不属于任何一个 job handler，所以以前只能写在
`app.py` 的 lifespan 里——那等于每加一个就改一次骨架。

## 约定

```python
# 在自己模块的 __init__.py 或 maintenance.py 里
@periodic("image.reconcile", every=300, immediate=True)
async def reconcile() -> None:
    ...
```

1. **名字前缀 = 模块名**（`image.*` / `task.*`），`app modules` 按前缀分组，
   `test_periodic_jobs_are_namespaced` 会拦住不守规矩的命名；
2. **handler 自己开会话**，不要在闭包里长期持有 session / 连接；
3. **必须幂等**：多副本部署时每个进程都会跑一份。确实「全集群只能跑一次」的任务
   （不可逆删除等）标 `leader_only=True`，调度器会向 `LeaderElector` 抢租约；
   租约策略由 `LEADER_ELECTOR` 选，默认 `local`（单实例）；
4. `every` 是**最小间隔**，不是精确定时：单个 handler 跑得久会推后别人的下一轮。

删除/新增一个周期任务只改模块文件，`app.py` 不认识任何具体任务——这是它存在的意义。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from core.coordination import LeaderElector, LocalLeaderElector
from core.logging import get_logger

logger = get_logger(__name__)

PeriodicHandler = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PeriodicJob:
    name: str
    every: float
    handler: PeriodicHandler
    immediate: bool = False
    # 不可逆 / 非幂等任务：先抢集群租约，抢不到就跳过这一轮
    leader_only: bool = False


_registry: dict[str, PeriodicJob] = {}


def periodic(
    name: str, *, every: float, immediate: bool = False, leader_only: bool = False
) -> Callable[[PeriodicHandler], PeriodicHandler]:
    """把协程函数注册为周期任务。返回原函数，便于直接调用（测试也用这条路）。"""
    if every < 1:
        raise ValueError(f"周期任务 {name} 的 every 必须 >= 1 秒，收到 {every}")
    if "." not in name:
        raise ValueError(f"周期任务 {name} 必须写成 `<模块>.<动作>`，否则 app modules 无法分组")

    def decorator(handler: PeriodicHandler) -> PeriodicHandler:
        if name in _registry and _registry[name].handler is not handler:
            raise ValueError(f"周期任务重名：{name}（两个模块抢同一个名字，只有一个会生效）")
        _registry[name] = PeriodicJob(
            name=name,
            every=every,
            handler=handler,
            immediate=immediate,
            leader_only=leader_only,
        )
        return handler

    return decorator


def periodic_jobs() -> tuple[PeriodicJob, ...]:
    return tuple(sorted(_registry.values(), key=lambda job: job.name))


def reset_periodic_jobs() -> None:
    """仅测试使用。"""
    _registry.clear()


def restore_periodic_jobs(jobs: Sequence[PeriodicJob]) -> None:
    """把注册表恢复成给定快照（测试夹具用）。

    `reset_periodic_jobs()` 是全局清空，某个用例调过之后，后续用例看到的
    `periodic_jobs()` 就是空的——「注册了但断言说没有」会变成随测试顺序漂移的
    假失败。夹具在每轮开始时用这里恢复，注册仍然只在模块 import 期发生一次。
    """
    _registry.clear()
    for job in jobs:
        _registry[job.name] = job


class Scheduler:
    """单进程内的周期调度器。

    `run_once(now)` 与 `serve(stop)` 分开，是为了让单测不必等真实时间：
    传一个假的 `now` 就能验证「到点了 / 没到点 / 一个 handler 抛异常不影响别人」。
    """

    def __init__(
        self,
        jobs: Sequence[PeriodicJob] | None = None,
        elector: LeaderElector | None = None,
    ) -> None:
        self._jobs = list(periodic_jobs() if jobs is None else jobs)
        self._last: dict[str, float] = {}
        # 不传就用单机选主器：直接构造的 Scheduler（测试、CLI）行为与以前一致
        self._elector: LeaderElector = elector if elector is not None else LocalLeaderElector()

    def start(self, now: float) -> None:
        """确定起点：非 immediate 的任务从「现在」开始计满一个 `every` 才跑。

        不记这个起点的话，`due()` 里 `last is None` 永远为真、又永远不算到期，
        任务就再也不会执行——一个安静的「注册了但从不运行」。
        """
        for job in self._jobs:
            if job.name not in self._last and not job.immediate:
                self._last[job.name] = now

    def due(self, now: float) -> list[PeriodicJob]:
        due: list[PeriodicJob] = []
        for job in self._jobs:
            last = self._last.get(job.name)
            if last is None:
                if job.immediate:
                    due.append(job)
                continue
            if now - last >= job.every:
                due.append(job)
        return due

    async def run_once(self, now: float) -> list[str]:
        """执行所有到期任务，返回实际执行的名字（按注册顺序）。"""
        executed: list[str] = []
        for job in self.due(now):
            # 先记账再执行：handler 里 raise 不该让同一轮无限重试，
            # 也不该把这个任务饿死到下次启动
            self._last[job.name] = now
            acquired = False
            if job.leader_only:
                acquired = self._elector.acquire(job.name, ttl=max(60.0, job.every * 2))
                if not acquired:
                    logger.debug("周期任务 %s 未抢到集群租约，跳过本轮", job.name)
                    continue
            try:
                await job.handler()
            except Exception:  # noqa: BLE001 - 一个任务炸掉不能拖垮调度器
                logger.exception("周期任务失败：%s", job.name)
                continue
            finally:
                if acquired:
                    self._elector.release(job.name)
            executed.append(job.name)
        return executed

    def seconds_until_next(self, now: float) -> float | None:
        """距离下一个到期还有多久；没有任务时返回 None。"""
        waits: list[float] = []
        for job in self._jobs:
            last = self._last.get(job.name)
            if last is None:
                waits.append(0.0)  # immediate 且还没跑
                continue
            waits.append(max(0.0, job.every - (now - last)))
        return min(waits) if waits else None

    async def serve(self, stop: asyncio.Event) -> None:
        logger.info(
            "周期任务调度器启动：%d 个 [%s]",
            len(self._jobs),
            ", ".join(j.name for j in self._jobs),
        )
        self.start(time.monotonic())
        while not stop.is_set():
            await self.run_once(time.monotonic())
            delay = self.seconds_until_next(time.monotonic())
            if delay is None:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(1.0, delay))
            except TimeoutError:
                continue
        logger.info("周期任务调度器已停止")
