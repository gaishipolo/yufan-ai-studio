"""故障注入：依赖挂掉时，系统的行为必须**符合设计**，而不是「看起来还行」。

## 为什么要专门一组用例

653 个用例里绝大多数验证的是「依赖正常时结果对不对」。但线上出事的形态不是这样：
数据库抖动一下、共享盘掉一次、缓存连不上几分钟。这些时刻系统是**降级运行**还是
**悄悄停摆**，决定了「小故障」会不会变成「整站不可用」。

这组用例断言的都是可以直接观察到的东西：

| 故障 | 期望行为 | 为什么 |
|---|---|---|
| 数据库不可用 | `/health/ready` → **503** | 编排系统只看状态码，200 等于「继续给我流量」 |
| 只有缓存不可用 | `/health/ready` → 200 + `degraded` | 能服务但在漏，摘实例反而更容易雪崩 |
| 队列驱动抛异常 | worker 协程**不退出**、退避后继续取 | 一次抖动就让后台全停，且恢复后不自愈 |
| 任务回执（ack/nack）失败 | 协程同样不退出 | 回执失败是队列的问题，不是「这个 worker 该死了」 |
| 备份失败 | 周期任务不抛、清理照跑 | 备份失败不该拖垮别的运维动作 |

这些用例同时也是**回归护栏**：任何「顺手把 except 去掉」的改动都会在这里变红。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from infra.queue.base import Job
from workers import Worker


class _DeadSession:
    """数据库完全不可用时的会话：任何查询都炸。

    只实现探针会碰到的两个方法——多写一个都会变成「在测试里替数据库干活」，
    那就测不到真实行为了。
    """

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("connection refused")

    async def rollback(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# 探针的状态码
# --------------------------------------------------------------------------- #
async def test_ready_returns_503_when_database_down() -> None:
    """`down` 必须落到状态码上。

    这一条曾经是 bug：响应体里写着 `status=down`，HTTP 却返回 200。
    编排系统读的是状态码，于是数据库断连的实例继续接流量，探针的结论完全没用上。
    """
    from fastapi import Response

    from modules.health.router import ready

    response = Response()
    payload = await ready(session=_DeadSession(), response=response)  # type: ignore[arg-type]

    assert response.status_code == 503
    assert payload.data is not None
    assert payload.data.status == "down"
    assert payload.data.database == "down"
    # 具体哪一项挂了要能从响应里读出来，不能只给一个 503
    assert any(
        check.name == "database" and not check.ok for check in payload.data.checks
    )


def test_ready_503_through_http_layer(client) -> None:
    """经过 FastAPI 的响应处理，确认状态码真的是 503。

    上面那条直接调路由函数，验证的是「我们设置了状态码」；这一条验证
    「框架真的把它发出去了」——两者中间的 `Response` 注入是容易写错的环节。
    """
    from db.session import get_session

    app = client.app  # type: ignore[attr-defined]

    async def _broken() -> Any:
        yield _DeadSession()

    app.dependency_overrides[get_session] = _broken
    try:
        response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 503
    body = response.json()["data"]
    assert body["status"] == "down"
    # 503 也必须是统一响应体，不能变成一个裸的 {"detail": ...}
    assert "checks" in body


def test_ready_keeps_200_when_only_cache_down(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存不可用只降级，不摘实例——它的影响是吞吐，不是正确性。"""

    import infra

    def _boom() -> Any:
        raise RuntimeError("cache down")

    monkeypatch.setattr(infra, "get_cache", _boom)

    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200, "缓存故障被当成 down 会把整批实例一起摘掉"
    body = response.json()["data"]
    assert body["status"] == "degraded"
    assert body["database"] == "ok"
    cache_check = next(c for c in body["checks"] if c["name"] == "cache")
    assert cache_check["ok"] is False
    assert cache_check["required"] is False


# --------------------------------------------------------------------------- #
# worker 的存活
# --------------------------------------------------------------------------- #
class _FlakyQueue:
    """前 `fail_times` 次取任务抛异常，之后正常返回空。"""

    # 协议成员：进程内替身没有「被别人接手」这回事，因此不续租
    lease_sec: float | None = None

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def dequeue(self, *, timeout: float = 0) -> Job | None:  # noqa: ASYNC109
        self.calls += 1
        # 让出控制权：真实驱动都有 await（等条件变量 / 查库），
        # 不 await 的 implementation 会让 `_loop` 变成忙等，连 cancel 都插不进去。
        await asyncio.sleep(0)
        if self.calls <= self.fail_times:
            raise RuntimeError("database is locked")
        return None

    async def ack(self, job: Job) -> None:
        return None

    async def renew(self, job: Job) -> bool:
        return True

    async def nack(self, job: Job, *, requeue: bool = False) -> None:
        return None


async def test_worker_survives_dequeue_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """队列连续抛异常时，消费协程必须活着并在退避后继续取。

    如果异常冒出去，`create_task` 建的协程就结束了，除停机外没人会重启它：
    进程活着、探针绿、后台任务全停——这类故障不会进任何告警。
    """
    import workers as workers_mod

    # 退避压缩到毫秒级，否则这条用例要等 3.5 秒
    monkeypatch.setattr(workers_mod, "DEQUEUE_BACKOFF_BASE", 0.001)
    monkeypatch.setattr(workers_mod, "DEQUEUE_BACKOFF_MAX", 0.005)

    queue = _FlakyQueue(fail_times=3)
    worker = Worker(queue, concurrency=1)  # type: ignore[arg-type]
    task = asyncio.create_task(worker._loop(0))
    try:
        await asyncio.sleep(0.1)
        assert not task.done(), "取任务失败让 worker 退出了"
        assert queue.calls >= 4, "退避之后应该继续尝试取任务"
    finally:
        worker._stopping.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_worker_survives_ack_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """回执失败是队列的问题，不是「这个 worker 该死了」。"""

    import workers as workers_mod

    class _AckBoomQueue(_FlakyQueue):
        async def dequeue(self, *, timeout: float = 0) -> Job | None:  # noqa: ASYNC109
            self.calls += 1
            if self.calls > 1:
                return None
            return Job(name="fault.demo", payload={})

        async def ack(self, job: Job) -> None:
            raise RuntimeError("ack failed")

    async def _handler(job: Job) -> None:
        return None

    monkeypatch.setitem(workers_mod.HANDLERS, "fault.demo", _handler)
    queue = _AckBoomQueue(fail_times=0)
    worker = Worker(queue, concurrency=1)  # type: ignore[arg-type]
    task = asyncio.create_task(worker._loop(0))
    try:
        await asyncio.sleep(0.1)
        assert not task.done(), "回执异常让 worker 退出了"
        assert queue.calls >= 2, "处理完一条之后应该继续取"
    finally:
        worker._stopping.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --------------------------------------------------------------------------- #
# 周期任务
# --------------------------------------------------------------------------- #
async def test_backup_failure_does_not_raise(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """备份失败只记日志：它不能把「清理过期备份」也一起拖掉。"""
    from core.config import settings
    from db import backup

    monkeypatch.setattr(settings, "backup_auto_enabled", True)
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "backup_retention_days", 7)

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise backup.BackupError("磁盘满了")

    monkeypatch.setattr(backup, "create_backup", _boom)

    # 不抛就是通过：调度器靠这个保证「一个任务炸掉不拖垮整轮」
    await backup.backup_loop()


# --------------------------------------------------------------------------- #
# 协程存活与忙碌度（T31）
# --------------------------------------------------------------------------- #
async def test_worker_exposes_consumer_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """「worker 还在不在跑」要能被直接抓到，而不是靠任务数不涨反推。

    探针是浅检查，永远不会因为 worker 停了而变红。没有这两个 gauge 时，唯一的
    线索是 `jobs_processed_total` 不涨 + 积压上升——要等到积压才有人发现，而且
    分不清「协程死了」与「协程卡在某个 handler 上」。
    """
    import workers as workers_mod

    async def _noop(job: Job) -> None:
        return None

    monkeypatch.setitem(workers_mod.HANDLERS, "fault.noop", _noop)

    before = workers_mod.WORKER_CONSUMERS.value(state="alive")
    worker = Worker(_FlakyQueue(fail_times=0), concurrency=2)  # type: ignore[arg-type]
    await worker.start()
    try:
        assert workers_mod.WORKER_CONSUMERS.value(state="desired") == 2
        # `start()` 只是 create_task，给循环一次被调度的机会
        await asyncio.sleep(0.05)
        assert workers_mod.WORKER_CONSUMERS.value(state="alive") == before + 2
        assert workers_mod.WORKER_BUSY.value() == 0, "空转时不该有人被算成在忙"
    finally:
        await worker.stop()

    assert workers_mod.WORKER_CONSUMERS.value(state="alive") == before, "停机后存活数没归零"


async def test_worker_busy_counts_running_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """`busy` 要能区分「活着但卡住」：长期等于并发数就是有 handler 卡住了。"""
    import workers as workers_mod

    started = asyncio.Event()
    release = asyncio.Event()

    async def _blocked(job: Job) -> None:
        started.set()
        await release.wait()

    monkeypatch.setitem(workers_mod.HANDLERS, "fault.blocked", _blocked)

    class _OneJob(_FlakyQueue):
        async def dequeue(self, *, timeout: float = 0) -> Job | None:  # noqa: ASYNC109
            self.calls += 1
            await asyncio.sleep(0)
            return Job(name="fault.blocked", payload={}) if self.calls == 1 else None

    worker = Worker(_OneJob(fail_times=0), concurrency=1)  # type: ignore[arg-type]
    task = asyncio.create_task(worker._loop(0))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert workers_mod.WORKER_BUSY.value() == 1, "正在跑的 job 没被计入 busy"
    finally:
        release.set()
        worker._stopping.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert workers_mod.WORKER_BUSY.value() == 0, "任务结束后 busy 没减回来"
