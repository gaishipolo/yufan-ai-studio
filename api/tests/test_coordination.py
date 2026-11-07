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


class _RacyStorage(_MemoryLeaseStorage):
    """能把同一份「过期租约」同时喂给多个实例：这正是竞态的交织点。

    真实事故里，A、B 都是先读到过期租约、再去删和建。串行调用两个 `acquire`
    复现不出来（后一个会读到前一个刚写的新租约），所以这里把读结果预置成快照。
    """

    def __init__(self) -> None:
        super().__init__()
        self.stale: list[Any] = []

    def prime(self, key: str, count: int) -> None:
        for _ in range(count):
            self.stale.append(copy.deepcopy(self.data[key]))

    def read(self, key: str) -> Any:
        if self.stale:
            return self.stale.pop(0)
        return super().read(key)


LEASE_KEY = "system/leases/asset.gc.json"


def test_two_instances_taking_over_an_expired_lease_only_one_wins() -> None:
    """接管过期租约必须是单赢家。

    原来的实现是「读到过期 → 删掉 → 重建」，删除不是原子的：
    B 先删后建拿到租约，A 再删就把 B 刚写的那份删了、然后自己建成功——
    两个实例都认为自己是 leader，一起跑不可逆任务。这条用例就是那个交织。
    """
    storage = _RacyStorage()
    first = BackendLeaderElector(storage=storage, instance_id="a")  # type: ignore[arg-type]
    second = BackendLeaderElector(storage=storage, instance_id="b")  # type: ignore[arg-type]
    assert first.acquire("asset.gc", ttl=0.001) is True
    storage.data[LEASE_KEY]["expiresAt"] = 0
    # 两边都先读到这份过期租约
    storage.prime(LEASE_KEY, 2)

    winners = [first.acquire("asset.gc"), second.acquire("asset.gc")]
    assert winners.count(True) == 1, f"接管出现了 {winners.count(True)} 个赢家"
    # 而且文件里那个人，必须就是自称赢了的那个
    assert storage.data[LEASE_KEY]["holder"] == ("a" if winners[0] else "b")


def test_late_contender_with_a_stale_read_cannot_steal_the_new_lease() -> None:
    """晚到的竞争者可能还握着一份很旧的读结果，它不能把新租约删掉。"""
    storage = _RacyStorage()
    first = BackendLeaderElector(storage=storage, instance_id="a")  # type: ignore[arg-type]
    assert first.acquire("asset.gc", ttl=0.001) is True
    storage.data[LEASE_KEY]["expiresAt"] = 0
    expired = copy.deepcopy(storage.data[LEASE_KEY])

    assert BackendLeaderElector(storage=storage, instance_id="b").acquire("asset.gc") is True
    # c 拿着过期快照来抢：必须失败，且不能删掉 b 刚写下的租约
    storage.stale.append(expired)
    assert BackendLeaderElector(storage=storage, instance_id="c").acquire("asset.gc") is False
    assert storage.data[LEASE_KEY]["holder"] == "b"


def test_a_crashed_takeover_does_not_lock_the_task_forever() -> None:
    """接管者崩在「删旧租约」和「写新租约」之间：标记过期后必须能被接手。

    没有这条兜底，一块永远不会过期的接管标记会把「每天跑一次的 GC」
    永久锁死，而且症状是「什么都没发生」。
    """
    storage = _RacyStorage()
    first = BackendLeaderElector(storage=storage, instance_id="a")  # type: ignore[arg-type]
    assert first.acquire("asset.gc", ttl=0.001) is True
    storage.data[LEASE_KEY]["expiresAt"] = 0
    # 模拟幽灵接管：标记写下了、旧租约没删、人崩了
    storage.data[BackendLeaderElector._ticket_key("asset.gc", 0.0)] = {
        "holder": "ghost",
        "takenAt": 0.0,
    }
    assert BackendLeaderElector(storage=storage, instance_id="b").acquire("asset.gc") is True
    assert storage.data[LEASE_KEY]["holder"] == "b"


def test_a_live_takeover_ticket_blocks_a_second_winner() -> None:
    """接管标记还在有效期内时，第二个人必须被挡住（这是单赢家的全部机制）。"""
    storage = _RacyStorage()
    first = BackendLeaderElector(storage=storage, instance_id="a")  # type: ignore[arg-type]
    assert first.acquire("asset.gc", ttl=0.001) is True
    storage.data[LEASE_KEY]["expiresAt"] = 0
    ticket = BackendLeaderElector._ticket_key("asset.gc", 0.0)
    storage.data[ticket] = {"holder": "a", "takenAt": 9e9}  # 有效期还没过

    assert BackendLeaderElector(storage=storage, instance_id="b").acquire("asset.gc") is False
    assert storage.data[LEASE_KEY]["expiresAt"] == 0  # 旧租约没被动过
