"""框架级启动恢复（R11-01）与审计启动自检（R11-05）。

恢复分成四类：续轮询 / 同步供应商孤儿 / 提交结果未知 / 建了记录没进队列。
最容易漏的是第二类：同步供应商的 `poll()` 恒返回 FAILED，照抄 task 的恢复器
会把一条本来该等人重试的记录判死，还多发一条失败事件。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from db.session import get_session_factory
from generation.recovery import (
    SYNC_PROVIDER_ORPHAN_MESSAGE,
    recover_declared,
    recover_kind,
)
from generation.spec import STATUS_FAILED, STATUS_QUEUED, STATUS_RUNNING
from modules.image.models import ImageJob

_OLD = datetime.now(tz=UTC) - timedelta(hours=2)


class _Runner:
    def __init__(self) -> None:
        self.resumed: list[str] = []
        self.dispatched: list[str] = []

    async def resume(self, kind: str, rid: str, *, delay: float = 0) -> None:
        self.resumed.append(rid)

    async def dispatch(self, kind: str, rid: str) -> str:
        self.dispatched.append(rid)
        return "ok"


def _router(
    monkeypatch: pytest.MonkeyPatch, *, supports_poll: bool | None, known: bool = True
) -> None:
    class _Provider:
        pass

    if supports_poll is not None:
        _Provider.supports_poll = supports_poll

    class _Spec:
        provider = "fake"

    class _Router:
        def resolve(self, model: str) -> _Spec | None:
            return _Spec() if known else None

        def provider_for(self, model: str) -> _Provider:
            return _Provider()

    monkeypatch.setattr("generation.recovery.get_model_router", lambda: _Router())


async def _insert(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "name": "recovery-probe",
        "prompt": "probe",
        "status": STATUS_QUEUED,
        "owner_id": "owner-1",
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


async def _delete(ids: list[str]) -> None:
    async with get_session_factory()() as session:
        for rid in ids:
            obj = await session.get(ImageJob, rid)
            if obj is not None:
                await session.delete(obj)
        await session.commit()


async def _status(rid: str) -> tuple[str, str | None]:
    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        return job.status, job.error_message


@pytest.fixture
def created_ids() -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    if ids:
        asyncio.run(_delete(ids))


@pytest.fixture(autouse=True)
def _needs_db(client: TestClient) -> None:
    """触发 session 级夹具：建表 + seed，后面才能直接开会话。"""


async def test_async_running_job_is_resumed_not_resubmitted(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(monkeypatch, supports_poll=True)
    rid = await _insert(status=STATUS_RUNNING, external_id="task-123")
    created_ids.append(rid)

    runner = _Runner()
    counts = await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    # 库里可能有别的测试留下的未终态记录，只断言自己造的这条
    assert counts["resume_poll"] >= 1
    assert rid in runner.resumed
    assert rid not in runner.dispatched, "已有 external_id 绝不重新提交（会二次计费）"


async def test_sync_provider_orphan_is_failed_not_resumed(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agnes 的 poll() 恒返回 FAILED，不能让 runner.resume 把它判死后再发事件。"""
    _router(monkeypatch, supports_poll=False)
    rid = await _insert(status=STATUS_RUNNING, external_id="task-sync")
    created_ids.append(rid)

    runner = _Runner()
    counts = await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    assert counts["sync_orphan"] >= 1
    assert rid not in runner.resumed
    assert rid not in runner.dispatched
    status, error = await _status(rid)
    assert status == STATUS_FAILED
    assert error == SYNC_PROVIDER_ORPHAN_MESSAGE


async def test_submit_unknown_is_dispatched_for_review(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(monkeypatch, supports_poll=True)
    rid = await _insert(submit_attempts=1)
    created_ids.append(rid)

    runner = _Runner()
    counts = await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    assert counts["needs_review"] >= 1
    assert rid in runner.dispatched, "交给 runner 的防线判死，框架不自己重提"
    assert rid not in runner.resumed


async def test_queued_without_attempt_is_resubmitted(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(monkeypatch, supports_poll=True)
    rid = await _insert()
    created_ids.append(rid)

    runner = _Runner()
    counts = await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    assert counts["resubmit"] >= 1
    assert rid in runner.dispatched


async def test_recover_declared_skips_modules_with_own_startup(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧模块有自己的恢复器时框架不插手，避免同一条记录被处置两次。"""
    _router(monkeypatch, supports_poll=True)
    rid = await _insert()
    created_ids.append(rid)

    class _Spec:
        recovery_kinds = ("image",)
        startup = object()

    assert await recover_declared([_Spec()]) == {}
    assert (await _status(rid))[0] == STATUS_QUEUED


async def test_submit_mode_snapshot_wins_over_current_catalog(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """记录自身的形态快照优先：供应商换配置不能把历史记录重新分类（R12-05）。"""
    _router(monkeypatch, supports_poll=False)  # 当前目录说「同步」
    rid = await _insert(
        status=STATUS_RUNNING, external_id="task-x", submit_mode="async"
    )
    created_ids.append(rid)

    runner = _Runner()
    await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    assert rid in runner.resumed, "快照说可轮询，就不能按当前目录置失败"
    assert rid not in runner.dispatched, "已有 external_id 绝不重新提交"


async def test_sync_snapshot_is_failed_not_resumed(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(monkeypatch, supports_poll=True)  # 当前目录说「可轮询」
    rid = await _insert(
        status=STATUS_RUNNING, external_id="task-x", submit_mode="sync"
    )
    created_ids.append(rid)

    runner = _Runner()
    await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    assert rid not in runner.resumed
    assert (await _status(rid))[0] == STATUS_FAILED


async def test_provider_without_supports_poll_defaults_to_not_pollable(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺省选安全的一边：新同步供应商忘了声明也不会被 resume 判死（R12-05）。"""
    _router(monkeypatch, supports_poll=None)
    rid = await _insert(status=STATUS_RUNNING, external_id="task-x")
    created_ids.append(rid)

    runner = _Runner()
    await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    assert rid not in runner.resumed
    assert (await _status(rid))[0] == STATUS_FAILED


async def test_unknown_model_without_snapshot_is_not_resumed(
    created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """老记录 + 当前目录已没这个模型：不许走 provider_for 的单供应商兜底（R12-05）。"""
    _router(monkeypatch, supports_poll=True, known=False)
    rid = await _insert(
        status=STATUS_RUNNING, external_id="task-x", model_id="retired-model"
    )
    created_ids.append(rid)

    runner = _Runner()
    await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    assert rid not in runner.resumed
    assert (await _status(rid))[0] == STATUS_FAILED


async def test_double_declaration_is_loud(caplog: Any) -> None:
    """同时声明 startup 与 recovery_kinds 时必须留痕，不能静默跳过（R12-07）。"""

    class _Spec:
        name = "both"
        recovery_kinds = ("image",)
        startup = object()

    with caplog.at_level("WARNING"):
        assert await recover_declared([_Spec()]) == {}
    assert "由模块自己的 startup 负责" in caplog.text


def test_audit_sink_is_catalogued() -> None:
    from api.ports import PORT_CATALOG, port_status

    assert any(spec.name == "audit-sink" for spec in PORT_CATALOG)
    names = [spec.name for spec, _ in port_status()]
    assert "audit-sink" in names, "启动日志必须能看到审计写入端有没有接上"


def test_prod_startup_requires_audit_sink() -> None:
    import core.audit as audit
    from db.audit_sink import write

    had_sink = audit.has_audit_sink()
    try:
        audit.reset_audit_sink()
        assert audit.ensure_audit_sink("test") is None, "非 prod 允许静默跳过"
        with pytest.raises(RuntimeError, match="审计写入端"):
            audit.ensure_audit_sink("prod")
        audit.set_audit_sink(write)
        assert audit.ensure_audit_sink("prod") is None
    finally:
        if had_sink:
            audit.set_audit_sink(write)
        else:
            audit.reset_audit_sink()
