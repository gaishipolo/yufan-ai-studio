"""集群选主与调度门控：不可逆任务不能靠「注释里写了要小心」来保证。

`BackendLeaderElector` 用归档存储的 `create()` 条件写抢租约，这里用内存假驱动
验证「同一时刻只有一个赢家 / 过期可接管 / 只释放自己的租约」，并验证调度器
在抢不到租约时确实跳过任务。
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from core.config import settings
from core.coordination import (
    BackendLeaderElector,
    LocalLeaderElector,
    get_leader_elector,
    reset_leader_elector,
)
from core.scheduler import PeriodicJob, Scheduler


class _MemoryLeaseStorage:
    """只实现选主需要的四个方法；`create` 用 dict 的 O_EXCL 语义。"""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def exists(self, key: str) -> bool:
        return key in self.data

    def read(self, key: str) -> Any:
        if key not in self.data:
            raise FileNotFoundError(key)
        return copy.deepcopy(self.data[key])

    def create(self, key: str, value: Any) -> bool:
        if key in self.data:
            return False
        self.data[key] = copy.deepcopy(value)
        return True

    def remove(self, key: str) -> None:
        self.data.pop(key, None)


def test_two_instances_only_one_wins() -> None:
    storage = _MemoryLeaseStorage()
    first = BackendLeaderElector(storage=storage, instance_id="a")  # type: ignore[arg-type]
    second = BackendLeaderElector(storage=storage, instance_id="b")  # type: ignore[arg-type]
    assert first.acquire("asset.gc") is True
    assert second.acquire("asset.gc") is False
    # 同一个实例续租不算抢别人的
    assert first.acquire("asset.gc") is True


def test_expired_lease_can_be_taken_over() -> None:
    storage = _MemoryLeaseStorage()
    first = BackendLeaderElector(storage=storage, instance_id="a")  # type: ignore[arg-type]
    second = BackendLeaderElector(storage=storage, instance_id="b")  # type: ignore[arg-type]
    assert first.acquire("asset.gc", ttl=0.001) is True
    storage.data["system/leases/asset.gc.json"]["expiresAt"] = 0
    assert second.acquire("asset.gc") is True
    assert storage.data["system/leases/asset.gc.json"]["holder"] == "b"


def test_release_only_removes_own_lease() -> None:
    storage = _MemoryLeaseStorage()
    first = BackendLeaderElector(storage=storage, instance_id="a")  # type: ignore[arg-type]
    second = BackendLeaderElector(storage=storage, instance_id="b")  # type: ignore[arg-type]
    assert first.acquire("asset.gc") is True
    second.release("asset.gc")
    assert storage.exists("system/leases/asset.gc.json")
    first.release("asset.gc")
    assert not storage.exists("system/leases/asset.gc.json")


def test_local_elector_always_acquires() -> None:
    assert LocalLeaderElector().acquire("anything") is True


def test_unknown_elector_mode_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "leader_elector", "bogus")
    reset_leader_elector()
    try:
        with pytest.raises(RuntimeError, match="LEADER_ELECTOR"):
            get_leader_elector()
    finally:
        reset_leader_elector()


class _NoLeader:
    def acquire(self, name: str, *, ttl: float = 60.0) -> bool:
        return False

    def release(self, name: str) -> None:
        return None


class _YesLeader(_NoLeader):
    def acquire(self, name: str, *, ttl: float = 60.0) -> bool:
        return True


def _job(leader_only: bool, calls: list[int]) -> PeriodicJob:
    async def handler() -> None:
        calls.append(1)

    return PeriodicJob(
        name="asset.gc", every=60.0, handler=handler, immediate=True, leader_only=leader_only
    )


def test_scheduler_skips_leader_only_job_without_lease() -> None:
    calls: list[int] = []
    scheduler = Scheduler(jobs=[_job(True, calls)], elector=_NoLeader())  # type: ignore[arg-type]
    assert asyncio.run(scheduler.run_once(0.0)) == []
    assert calls == []


def test_scheduler_runs_leader_only_job_with_lease() -> None:
    calls: list[int] = []
    scheduler = Scheduler(jobs=[_job(True, calls)], elector=_YesLeader())  # type: ignore[arg-type]
    assert asyncio.run(scheduler.run_once(0.0)) == ["asset.gc"]
    assert calls == [1]


def test_scheduler_leaves_plain_jobs_alone() -> None:
    calls: list[int] = []
    scheduler = Scheduler(jobs=[_job(False, calls)], elector=_NoLeader())  # type: ignore[arg-type]
    assert asyncio.run(scheduler.run_once(0.0)) == ["asset.gc"]
    assert calls == [1]
