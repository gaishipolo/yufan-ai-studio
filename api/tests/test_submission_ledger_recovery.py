"""提交台账 → 回填 `record.external_id` → 续接轮询（D5 第三步）。

## 这条线补的是哪个窗口

驱动的 `submit` 拿到上游标识的**同一个瞬间**就落台账；而业务表的
`record.external_id` 是 submit 返回**之后**才写。这中间崩了：

- 业务表说「没提交过」→ 恢复器判「提交结果未知」→ 判死等人核对；
- 台账说「提交过、凭证在这儿」。

于是上游已经在跑的任务白丢，用户等的是一条**已经生成好**的结果。回填把凭证
捡回来，记录就能照常续接轮询。

## 测的是语义，不是「某张表能读写」

- 台账是**追加式历史**：同一记录可能有多轮（4xx 后框架自动重试、人工重提）
  → 只认**最后一轮**；
- 回填**不复位** `submit_attempts`：那是「这次提交的结果还没落库」的证据，
  复位了后续失败会被当成「没提交过」而重新提交（重复计费）；
- 台账里没有可用凭证 → **保持现状**（判「提交结果未知」等人核对），不编一个值。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import generation.recovery as recovery_mod
import modules.task.worker as worker_mod
from core.ports.submission_ledger import (
    has_submission_ledger,
    lookup_recovery_external_id,
    register_submission_ledger,
    reset_submission_ledger,
)
from db.session import get_session_factory
from generation.recovery import recover_kind
from generation.spec import STATUS_QUEUED
from modules.image.models import ImageJob
from modules.model_driver.kit.submission_guard import find_recoverable_external_id
from modules.model_driver.models import SubmissionReceipt
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask

_OLD = datetime.now(tz=UTC) - timedelta(hours=2)


@pytest.fixture(autouse=True)
def _needs_db(client: TestClient) -> None:
    """触发 session 级夹具：建表 + seed，后面才能直接开会话。"""


@pytest.fixture
def clean_ids() -> Iterator[list[str]]:
    """本文件造出来的业务记录 id（测试结束清掉）。"""
    ids: list[str] = []
    yield ids
    if ids:
        import asyncio

        async def _cleanup() -> None:
            async with get_session_factory()() as session:
                for rid in ids:
                    obj = await session.get(ImageJob, rid)
                    if obj is not None:
                        await session.delete(obj)
                    task = await session.get(VideoTask, rid)
                    if task is not None:
                        await session.delete(task)
                await session.commit()

        asyncio.run(_cleanup())


class _Runner:
    def __init__(self) -> None:
        self.resumed: list[str] = []
        self.dispatched: list[str] = []

    async def resume(self, kind: str, rid: str, *, delay: float = 0) -> None:
        self.resumed.append(rid)

    async def dispatch(self, kind: str, rid: str) -> str:
        self.dispatched.append(rid)
        return "ok"


def _router(monkeypatch: pytest.MonkeyPatch, *, supports_poll: bool) -> None:
    class _Provider:
        pass

    _Provider.supports_poll = supports_poll

    class _Spec:
        provider = "fake"

    class _Router:
        def resolve(self, model: str) -> _Spec | None:
            return _Spec()

        def provider_for(self, model: str) -> _Provider:
            return _Provider()

    monkeypatch.setattr(recovery_mod, "get_model_router", lambda: _Router())


async def _insert_image(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "name": "ledger-probe",
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


async def _insert_task(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "title": "台账回填探针",
        "prompt": "极光",
        "status": TaskStatus.RUNNING.value,
        "model": "Seedance 2.5",
        "author_id": "u_001",
        "author_name": "老大",
    }
    base.update(overrides)
    task = VideoTask(**base)
    async with get_session_factory()() as session:
        session.add(task)
        await session.commit()
        return task.id


_UNSET = object()


async def _receipt(
    *,
    record_id: str,
    run_id: str,
    status: str,
    external_id: str | None = None,
    task_id: str | None | object = _UNSET,
    created_at: datetime | None = None,
) -> None:
    """写一行台账。

    `task_id` 显式给 `None` = 同步形态（没有独立任务号）；不传 = 与 `external_id`
    相同（异步形态）。
    """
    task_id_value = external_id if task_id is _UNSET else task_id
    async with get_session_factory()() as session:
        session.add(
            SubmissionReceipt(
                provider="agnes",
                run_id=run_id,
                record_id=record_id,
                model="agnes-image",
                status=status,
                task_id=task_id_value,
                external_id=external_id,
                channel_used="oss",
                created_at=created_at or datetime.now(tz=UTC),
            )
        )
        await session.commit()


async def _load_image(rid: str) -> ImageJob:
    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        return job


async def _load_task(task_id: str) -> VideoTask:
    async with get_session_factory()() as session:
        task = await session.get(VideoTask, task_id)
        assert task is not None
        return task


# ---------------------------------------------------------------------------
# kit 层：按业务记录 id 反查「最后一轮」的凭证
# ---------------------------------------------------------------------------


async def test_no_receipt_returns_none() -> None:
    """没有台账行 → 没有凭证（中性值，不编）。"""
    assert await find_recoverable_external_id("record-does-not-exist") is None


async def test_submitted_row_yields_its_credential() -> None:
    """只有一轮、且是「已提交」→ 返回那一轮的凭证。"""
    rid = "kit-single-round"
    await _receipt(
        record_id=rid,
        run_id=f"{rid}#1",
        status="submitted",
        external_id="ext-1",
        created_at=_OLD,
    )
    try:
        assert await find_recoverable_external_id(rid) == "ext-1"
    finally:
        await _delete_receipts(rid)


async def test_only_the_latest_round_counts() -> None:
    """台账是追加式历史：只认**最后一轮**。

    第一轮提交成功、第二轮提交结果未知 → 不回退到第一轮（那是上一轮的结果；
    人工重提的语义正是「要新的」）。
    """
    rid = "kit-two-rounds"
    await _receipt(
        record_id=rid,
        run_id=f"{rid}#1",
        status="submitted",
        external_id="ext-old",
        created_at=_OLD,
    )
    await _receipt(
        record_id=rid,
        run_id=f"{rid}#2",
        status="submission_unknown",
        external_id=None,
        created_at=_OLD + timedelta(minutes=1),
    )
    try:
        assert await find_recoverable_external_id(rid) is None
    finally:
        await _delete_receipts(rid)


async def test_latest_round_submitted_wins() -> None:
    """最后一轮是「已提交」→ 用它的凭证（不是第一轮的）。"""
    rid = "kit-resubmit"
    await _receipt(
        record_id=rid,
        run_id=f"{rid}#1",
        status="submission_unknown",
        external_id=None,
        created_at=_OLD,
    )
    await _receipt(
        record_id=rid,
        run_id=f"{rid}#2",
        status="submitted",
        external_id="ext-new",
        created_at=_OLD + timedelta(minutes=1),
    )
    try:
        assert await find_recoverable_external_id(rid) == "ext-new"
    finally:
        await _delete_receipts(rid)


async def test_sync_shape_receipt_is_not_recoverable() -> None:
    """同步形态（没有独立任务号）的台账行**不可回填**。

    回填了也撑不起「续接轮询」：只会把「提交结果未知，等人核对」变成
    「同步供应商孤儿，判失败」—— 语义退化。不回填时框架会禁止自动重提
    （避免二次计费），把人交给核对流程，这才是同步形态崩溃后该走的路。
    """
    rid = "kit-sync-shape"
    await _receipt(
        record_id=rid,
        run_id=f"{rid}#1",
        status="submitted",
        external_id="sync-snapshot-id",
        task_id=None,  # 同步形态：没有独立任务号
        created_at=_OLD,
    )
    try:
        assert await find_recoverable_external_id(rid) is None
    finally:
        await _delete_receipts(rid)


async def _delete_receipts(record_id: str) -> None:
    from sqlalchemy import delete

    async with get_session_factory()() as session:
        await session.execute(
            delete(SubmissionReceipt).where(SubmissionReceipt.record_id == record_id)
        )
        await session.commit()


async def test_framework_recovery_ignores_sync_shape_receipt(
    clean_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """台账里是同步形态的行 → 不回填，保持「交回 runner 判死等人核对」的路径。

    这里**不 monkeypatch 注入口** —— 形态判定在 kit 层（只有「有独立任务号」的行
    才给凭证），要连着真实注入口一起验。
    """
    _router(monkeypatch, supports_poll=True)
    rid = await _insert_image(status=STATUS_QUEUED, submit_attempts=1)
    clean_ids.append(rid)
    await _receipt(
        record_id=rid,
        run_id=f"{rid}#1",
        status="submitted",
        external_id="sync-snapshot-id",
        task_id=None,
        created_at=_OLD,
    )

    runner = _Runner()
    await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    job = await _load_image(rid)
    assert job.external_id is None, "同步形态的凭证撑不起续接，不该回填"
    assert rid in runner.dispatched, "保持原有「交给 runner 判死等人核对」的路径"
    await _delete_receipts(rid)


# ---------------------------------------------------------------------------
# 注入口：未注册时是中性值，不是 503
# ---------------------------------------------------------------------------


async def test_unregistered_port_returns_none() -> None:
    """注入口没接上 → 查不到凭证（None），恢复流程照常走「等人核对」。"""
    had = has_submission_ledger()
    reset_submission_ledger()
    try:
        assert await lookup_recovery_external_id("anything") is None
    finally:
        reset_submission_ledger()
        if had:
            from modules.model_driver.ledger import SubmissionLedgerFacade

            register_submission_ledger(SubmissionLedgerFacade())


async def test_registered_port_is_consulted() -> None:
    """注册后走实现（而不是永远返回 None）。"""
    had = has_submission_ledger()
    reset_submission_ledger()

    class _Fake:
        async def find_external_id(self, record_id: str) -> str | None:
            return "ext-from-port" if record_id == "r1" else None

    register_submission_ledger(_Fake())
    try:
        assert await lookup_recovery_external_id("r1") == "ext-from-port"
        assert await lookup_recovery_external_id("r2") is None
    finally:
        reset_submission_ledger()
        if had:
            from modules.model_driver.ledger import SubmissionLedgerFacade

            register_submission_ledger(SubmissionLedgerFacade())


# ---------------------------------------------------------------------------
# 框架恢复器：回填 → 续接轮询（图片链）
# ---------------------------------------------------------------------------


async def test_framework_recovery_backfills_and_resumes(
    clean_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """崩在「拿到上游标识 → 写业务表」之间的记录：回填凭证后走续轮询。

    这条是本工作包的核心断言：**不重复提交**（绝不 dispatch）、**结果不丢**
    （resume 续查）。
    """
    _router(monkeypatch, supports_poll=True)
    rid = await _insert_image(status=STATUS_QUEUED, submit_attempts=1, submit_mode="async")
    clean_ids.append(rid)
    await _receipt(
        record_id=rid,
        run_id=f"{rid}#1",
        status="submitted",
        external_id="task-from-ledger",
        created_at=_OLD,
    )

    async def _fake_lookup(record_id: str) -> str | None:
        return "task-from-ledger" if record_id == rid else None

    monkeypatch.setattr(recovery_mod, "lookup_recovery_external_id", _fake_lookup)

    runner = _Runner()
    await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    job = await _load_image(rid)
    assert job.external_id == "task-from-ledger", "凭证必须回填到业务表"
    assert job.submit_attempts == 1, "回填不复位提交计数（复位会让后续失败被重复提交）"
    assert rid in runner.resumed, "回填后应续接轮询"
    assert rid not in runner.dispatched, "有凭证了绝不重新提交（会二次计费）"
    await _delete_receipts(rid)


async def test_framework_recovery_without_ledger_keeps_review_path(
    clean_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """台账里没有可用凭证 → 保持现状：交回 runner 判「提交结果未知」等人核对。"""
    _router(monkeypatch, supports_poll=True)
    rid = await _insert_image(status=STATUS_QUEUED, submit_attempts=1)
    clean_ids.append(rid)

    async def _fake_lookup(record_id: str) -> str | None:
        return None

    monkeypatch.setattr(recovery_mod, "lookup_recovery_external_id", _fake_lookup)

    runner = _Runner()
    await recover_kind("image", runner=runner)  # type: ignore[arg-type]

    job = await _load_image(rid)
    assert job.external_id is None, "没有凭证就不许编一个"
    assert rid in runner.dispatched, "保持原有「交给 runner 判死等人核对」的路径"


# ---------------------------------------------------------------------------
# 任务链自己的恢复器：回填 → 续轮询（视频链）
# ---------------------------------------------------------------------------


async def test_task_worker_backfills_and_resumes_poll(
    clean_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """视频链：台账里的凭证回填到 `VideoTask.external_id`，只续轮询不重提。"""
    task_id = await _insert_task(submit_attempts=1)
    clean_ids.append(task_id)

    async def _fake_lookup(record_id: str) -> str | None:
        return "task-from-ledger" if record_id == task_id else None

    monkeypatch.setattr(worker_mod, "lookup_recovery_external_id", _fake_lookup)

    runner = _Runner()
    monkeypatch.setattr(worker_mod, "get_runner", lambda queue: runner)

    await worker_mod.resume_unfinished_tasks(_FakeQueue())

    task = await _load_task(task_id)
    assert task.external_id == "task-from-ledger", "凭证必须回填到任务表"
    assert task.status == TaskStatus.RUNNING.value
    assert task.submit_attempts == 1, "回填不复位提交计数"
    assert task_id in runner.resumed, "回填后只续轮询"
    assert task_id not in runner.dispatched, "有凭证了绝不重新提交（会二次计费）"


async def test_task_worker_without_ledger_still_fails_unknown(
    clean_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """台账里没有可用凭证 → 仍然判「提交结果未知」等人核对（原有语义不变）。"""
    task_id = await _insert_task(submit_attempts=1)
    clean_ids.append(task_id)

    async def _fake_lookup(record_id: str) -> str | None:
        return None

    monkeypatch.setattr(worker_mod, "lookup_recovery_external_id", _fake_lookup)

    runner = _Runner()
    monkeypatch.setattr(worker_mod, "get_runner", lambda queue: runner)

    await worker_mod.resume_unfinished_tasks(_FakeQueue())

    task = await _load_task(task_id)
    assert task.external_id is None
    assert task.status == TaskStatus.FAILED.value
    assert task_id not in runner.resumed


class _FakeQueue:
    """任务恢复器只把 queue 转交给 runner，这里用不到真实队列。"""

    def snapshot(self) -> list[dict[str, Any]]:
        return []
