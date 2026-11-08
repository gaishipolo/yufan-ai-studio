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
4. `every` 是**最小间隔**，不是精确定时：单个 handler 跑得久会推后别人的下一轮；
5. **handler 有上限**（默认 `max(60, every * 2)`，与租约 TTL 同一个数）。跑到上限
   就被取消并记一条 error，调度器接着跑下一个。

第 5 条不是洁癖：`run_once` 是顺序 await 的，一个**回不来**的 handler（卡住的
`pg_dump`、没有读超时的第三方调用、等待一个永远不会来的锁）会让这个进程里的
所有周期任务永久停摆——outbox 不再投递、生图补偿不再重投、素材 GC 不再跑，
而日志里一条错误都没有，因为「抛出来的」被捕获了，「回不来的」从来没人管。
上限取 `every * 2` 是为了和租约对齐：租约到期别人可以接管，我们必须在那个
时刻之前停手，否则同一件不可逆的事会被两个进程各做一遍。

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
    # 单次执行上限（秒）。`None` = 按 `every` 推导，见 `handler_timeout()`。
    # 只该在「这个任务天生比它的周期还长」时显式写死，否则留着让默认值跟随 every。
    timeout: float | None = None


def handler_timeout(job: PeriodicJob) -> float:
    """单次执行上限，同时也是它的租约 TTL。

    **两个数必须是同一个**：租约到期后别的实例就能接管这个任务，而此时老 handler
    如果还在跑，同一件「不可逆」的事就被做了两遍。把它们写成两个各自可调的参数，
    迟早会有人只改其中一个。
    """
    if job.timeout is not None and job.timeout > 0:
        return float(job.timeout)
    return max(60.0, job.every * 2)


_registry: dict[str, PeriodicJob] = {}


def periodic(
    name: str,
    *,
    every: float,
    immediate: bool = False,
    leader_only: bool = False,
    timeout: float | None = None,
) -> Callable[[PeriodicHandler], PeriodicHandler]:
    """把协程函数注册为周期任务。返回原函数，便于直接调用（测试也用这条路）。"""
    if every < 1:
        raise ValueError(f"周期任务 {name} 的 every 必须 >= 1 秒，收到 {every}")
    if "." not in name:
        raise ValueError(f"周期任务 {name} 必须写成 `<模块>.<动作>`，否则 app modules 无法分组")
    if timeout is not None and timeout <= 0:
        raise ValueError(f"周期任务 {name} 的 timeout 必须为正数，收到 {timeout}")

    def decorator(handler: PeriodicHandler) -> PeriodicHandler:
        if name in _registry and _registry[name].handler is not handler:
            raise ValueError(f"周期任务重名：{name}（两个模块抢同一个名字，只有一个会生效）")
        _registry[name] = PeriodicJob(
            name=name,
            every=every,
            handler=handler,
            immediate=immediate,
            leader_only=leader_only,
            timeout=timeout,
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

    async def _acquire(self, name: str, ttl: float) -> bool:
        """抢集群租约，**在线程池里**调。

        `LeaderElector` 的实现是同步的，因为它的原语（文件 `O_EXCL`、S3 的
        `If-None-Match: *`）本身是同步的。`LEADER_ELECTOR=backend` +
        `ARCHIVE_DRIVER=s3` 时，`acquire` 里是若干次 boto3 往返（`head` / `put`
        各几十到几百毫秒，接管路径更多）。直接在协程里 await 同步函数不会报错，
        但会把**整个事件循环**按住那么久：这段时间里所有 HTTP 请求、其它周期任务、
        队列消费全部停摆，而日志里一条异常都没有——表现为「服务偶尔卡一下」，
        排查方向会完全跑偏到数据库或网络。

        所以阻塞调用必须离开事件循环。用默认执行器（`asyncio.to_thread`）而不是
        自建线程池：这里一周期才几次，与口令哈希那种要限并发的场景不是一回事。

        抢租约本身失败（存储不可达）按「没抢到」处理——`_elector` 的实现已经
        吞掉各自的异常，这里再包一层是为了挡住自定义实现漏吞的情况。
        """
        try:
            return await asyncio.to_thread(self._elector.acquire, name, ttl=ttl)
        except Exception:  # noqa: BLE001 - 抢不到就是这一轮不跑，失败方向是安全
            logger.warning("抢集群租约失败，跳过本轮：%s", name)
            return False

    async def _release(self, name: str) -> None:
        """释放租约，同样离开事件循环。失败不影响调度：租约本来就会 TTL 过期。"""
        try:
            await asyncio.to_thread(self._elector.release, name)
        except Exception:  # noqa: BLE001 - TTL 兜底，释放失败只是多占一会儿
            logger.debug("释放集群租约失败（等 TTL 过期）：%s", name)

    async def run_once(self, now: float) -> list[str]:
        """执行所有到期任务，返回实际执行的名字（按注册顺序）。"""
        executed: list[str] = []
        for job in self.due(now):
            # 先记账再执行：handler 里 raise 不该让同一轮无限重试，
            # 也不该把这个任务饿死到下次启动
            self._last[job.name] = now
            timeout = handler_timeout(job)
            acquired = False
            if job.leader_only:
                acquired = await self._acquire(job.name, timeout)
                if not acquired:
                    logger.debug("周期任务 %s 未抢到集群租约，跳过本轮", job.name)
                    continue
            try:
                await asyncio.wait_for(job.handler(), timeout=timeout)
            except TimeoutError:
                # 单独一条日志：这是「代码没错、但它就是回不来」，和抛异常是两回事
                # （超时多半是外部依赖卡住，异常多半是逻辑错）。两者排查方向完全不同。
                logger.error(
                    "周期任务超时（>%.0f 秒）已取消：%s —— 期间本轮其它任务被推迟，"
                    "且租约已到期，别的实例可能已接管",
                    timeout,
                    job.name,
                )
                continue
            except Exception:  # noqa: BLE001 - 一个任务炸掉不能拖垮调度器
                logger.exception("周期任务失败：%s", job.name)
                continue
            finally:
                if acquired:
                    await self._release(job.name)
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
