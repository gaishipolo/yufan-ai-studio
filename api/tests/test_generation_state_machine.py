"""跨组件状态机契约表（R12 第八节）。

R11-01 的恢复器与 R11-02 的 retry 出口分开实现、各自测试都绿，
但没人把两个动作作用到**同一条记录**上跑一遍，结果组合出「带着
external_id 重投 = 第二次计费」这条路径。组件级用例天然覆盖不到
跨组件的状态交接，所以这里用一张表按 (status, external_id, attempts,
submit_mode) 枚举关键状态，对恢复 / 巡检 / retry 三个动作逐格断言：
终态、是否允许重新提交、以及对账凭证是否保留。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import generation.recovery as recovery
import modules.image.service as image_service
from core.exceptions import InvalidState
from db.session import get_session_factory
from generation.recovery import recover_kind
from generation.spec import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from modules.image.models import ImageJob
from modules.image.service import ImageService

_OLD = datetime.now(tz=UTC) - timedelta(hours=2)


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def resume(self, kind: str, rid: str, *, delay: float = 0) -> None:
        self.calls.append(("resume", rid))

    async def dispatch(self, kind: str, rid: str) -> str:
        self.calls.append(("dispatch", rid))
        return "ok"


class _Actor:
    id = "u1"

    def has(self, code: str) -> bool:
        return False


def _router(monkeypatch: pytest.MonkeyPatch, *, supports_poll: bool) -> None:
    class _Provider:
        pass

    _Provider.supports_poll = supports_poll

    class _Spec:
        provider = "fake"

    class _Router:
        def resolve(self, model: str) -> _Spec:
            return _Spec()

        def provider_for(self, model: str) -> _Provider:
            return _Provider()

    monkeypatch.setattr(recovery, "get_model_router", lambda: _Router())


async def _insert(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "name": "state-machine",
        "prompt": "p",
        "status": STATUS_QUEUED,
        "owner_id": "u1",
        "model": "echo",
        "model_id": "echo-image",
        "created_at": _OLD,
    }
    base.update(overrides)
    job = ImageJob(**base)
    async with get_session_factory()() as session:
        session.add(job)
        await session.commit()
        return job.id


async def _state(rid: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        return {
            "status": job.status,
            "external_id": job.external_id,
            "previous_external_id": job.previous_external_id,
            "submit_attempts": job.submit_attempts,
            "submit_mode": job.submit_mode,
        }


async def _delete(ids: list[str]) -> None:
    async with get_session_factory()() as session:
        for rid in ids:
            obj = await session.get(ImageJob, rid)
            if obj is not None:
                await session.delete(obj)
        await session.commit()


@pytest.fixture
def created_ids() -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    if ids:
        asyncio.run(_delete(ids))


@pytest.fixture(autouse=True)
def _needs_db(client: TestClient) -> None:
    """触发 session 级夹具：建表 + seed。"""


@pytest.mark.parametrize(
    ("status", "external_id", "attempts", "mode", "expect"),
    [
        (STATUS_RUNNING, "task-x", 0, "sync", "sync_orphan"),
        (STATUS_RUNNING, "task-x", 0, "async", "resume_poll"),
        (STATUS_RUNNING, None, 1, None, "needs_review"),
        (STATUS_QUEUED, None, 0, None, "resubmit"),
        (STATUS_FAILED, None, 1, None, "terminal"),
        (STATUS_SUCCEEDED, "task-x", 0, "sync", "terminal"),
    ],
)
async def test_recovery_decision_table(
    created_ids: list[str],
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    external_id: str | None,
    attempts: int,
    mode: str | None,
    expect: str,
) -> None:
    _router(monkeypatch, supports_poll=True)
    rid = await _insert(
        status=status,
        external_id=external_id,
        submit_attempts=attempts,
        submit_mode=mode,
    )
    created_ids.append(rid)
    runner = _Runner()
    await recover_kind("image", runner=runner)

    ops = [op for op, target in runner.calls if target == rid]
    after = await _state(rid)
    if expect == "sync_orphan":
        assert ops == []
        assert after["status"] == STATUS_FAILED
        assert after["external_id"] == "task-x", "凭证必须留着对账"
    elif expect == "resume_poll":
        assert ops == ["resume"], "可续查的绝不重新提交"
        assert after["status"] == STATUS_RUNNING
    elif expect in ("needs_review", "resubmit"):
        assert ops == ["dispatch"], "交给 runner 的防线，框架不自己判死"
    else:
        assert ops == [], "终态记录不参与恢复"
        assert after["status"] == status


async def test_charged_orphan_recovery_then_retry(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """把 R11-01 与 R11-02 串起来：这是唯一一条会重复计费的交接路径。"""
    _router(monkeypatch, supports_poll=False)
    rid = await _insert(
        status=STATUS_RUNNING,
        external_id="agnes-task-REAL-123",
        submit_mode="sync",
    )
    created_ids.append(rid)
    runner = _Runner()
    await recover_kind("image", runner=runner)
    # 恢复器会捞到库里其它测试遗留的未终态记录，只断言自己造的这条
    assert all(target != rid for _, target in runner.calls), "恢复阶段绝不重新提交"
    assert (await _state(rid))["status"] == STATUS_FAILED

    # 用户照着失败文案点「重试」：默认必须挡下来，凭证也别动
    async with get_session_factory()() as session:
        with pytest.raises(InvalidState, match="可能已产生费用"):
            await ImageService(session).retry(_Actor(), rid)  # type: ignore[arg-type]
    assert (await _state(rid))["external_id"] == "agnes-task-REAL-123"
    assert all(target != rid for _, target in runner.calls)

    # 显式确认后才允许第二次 submit，且凭证在记录上留痕
    monkeypatch.setattr(image_service, "get_runner", lambda: runner)
    async with get_session_factory()() as session:
        await ImageService(session).retry(
            _Actor(), rid, confirm_charged=True  # type: ignore[arg-type]
        )
    after = await _state(rid)
    assert after["status"] == STATUS_QUEUED
    assert after["external_id"] is None
    assert after["previous_external_id"] == "agnes-task-REAL-123"
    assert after["submit_attempts"] == 0
    assert [op for op, target in runner.calls if target == rid] == ["dispatch"], (
        "确认后只提交一次"
    )


async def test_reconcile_never_redispatches_submitted_records(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12-06：周期巡检捞 running，但拿到供应商任务号的绝不重投。"""
    import modules.image.maintenance as maintenance

    runner = _Runner()
    monkeypatch.setattr(maintenance, "get_runner", lambda: runner)
    submitted = await _insert(
        status=STATUS_RUNNING,
        external_id="task-x",
        started_at=_OLD,
        submit_mode="async",
    )
    stuck = await _insert(
        status=STATUS_RUNNING, submit_attempts=1, started_at=_OLD
    )
    created_ids.extend([submitted, stuck])

    await maintenance.reconcile_stale_jobs()

    dispatched = [target for op, target in runner.calls if op == "dispatch"]
    assert submitted not in dispatched, "已受理的任务重投就是第二份账单"
    assert stuck in dispatched, "卡在 running 且没拿到任务号的要捞回来"
