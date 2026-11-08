"""恢复链：谁能重提、谁只能等回执、谁该被放手。

重建之后「一条替换任务」的真相在数据库行上，恢复器的判断也从「读 `run.json`
猜」变成「看四个字段的组合」：`task_id` / `submit_attempts` / `status` /
`submit_mode`。这一组用例把每个组合钉一遍，因为**判错的后果都是钱**：

- 有 `task_id` 还去提交 → 第二次计费；
- `submit_attempts>0` 却没有 `task_id`，说明提交动作可能已经送达，自动重提等于
  赌一把「上次没成功」；
- `success` / `failed` / `submission_unknown` 被启动扫描翻回 `running` →
  恢复集合永不收敛，每重启一次就多查一次上游。

`_wait_for_release` 与 `resume()` 都在这些判据外面：前者负责「抢不到锁时退避重投，
六轮之后放手」，后者负责「只挑还有事可做的行」。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from tests_template_helpers import (
    ADMIN,
    add_run,
    drop_run,
    read_run,
    workspace,
)

from core.config import settings
from core.exceptions import Conflict, UpstreamError
from db.session import get_session_factory
from modules.template import worker
from modules.template.pipeline import ReplacementStore
from modules.template.repository import RunRepository


@asynccontextmanager
async def _session() -> AsyncIterator[object]:
    """每次调用一个独立事务：生产里 worker 与 HTTP 也不共享事务边界。"""
    async with get_session_factory()() as session:
        yield session


async def _switch(run_id: str, mode: str) -> None:
    async with _session() as session:
        await worker.set_billing_mode(session, run_id, ADMIN, mode)


async def _schedule_state(run_id: str) -> str:
    async with _session() as session:
        return await worker.schedule_state(session, run_id, ADMIN)


async def _recover_state(run_id: str) -> str:
    async with _session() as session:
        return await worker.recover_state(session, run_id)


async def _alive(run_id: str) -> None:
    """造一条「能被投影」的任务：行 + 目录 + `run.json` + 指针。"""
    workspace(run_id)


# --------------------------------------------------------------------------- #
# 计费模式：只影响本任务，且只在提交前
# --------------------------------------------------------------------------- #
async def test_billing_mode_switch_only_before_submission() -> None:
    """已交给上游的任务不能再切成积分队列，同模式重发不算切换。"""
    pending = await add_run(status="uploaded", billing_mode="free")
    submitted = await add_run(status="running", billing_mode="free", task_id="upstream-1")
    try:
        await _switch(pending.id, "credits")
        assert (await read_run(pending.id)).billing_mode == "credits"

        with pytest.raises(Conflict) as raised:
            await _switch(submitted.id, "credits")
        assert "免费队列" in str(raised.value)
        assert (await read_run(submitted.id)).billing_mode == "free"
    finally:
        await drop_run(pending.id)
        await drop_run(submitted.id)


async def test_switching_to_credits_releases_waiting_free() -> None:
    """点了加速却还停在原地，看起来就像按钮没反应。

    `waiting_free` 是「免费队列报价非 0」卡住的，换成积分队列后这个理由消失了，
    所以要一并把状态推回 `uploaded`；那条 0 积分的旧报价也必须清掉——它在积分
    队列里没有任何意义，留着只会让核对页显示「预计 0 积分」。
    """
    run = await add_run(
        status="waiting_free",
        billing_mode="free",
        estimated_credits=0,
        last_error="免费队列排队中",
        framework_status="queued",
    )
    try:
        await _switch(run.id, "credits")
        row = await read_run(run.id)
        assert (row.status, row.framework_status) == ("uploaded", "queued")
        assert row.estimated_credits is None
        assert row.last_error is None
        assert row.billing_mode == "credits"
    finally:
        await drop_run(run.id)


async def test_switching_to_the_same_mode_keeps_the_quote() -> None:
    """同模式重发是幂等的：不能顺手把 `estimated_credits` 清掉。

    前端重新打开任务时会带上记录里的模式再发一次，那不是切换。清掉报价会让
    「预计消耗」在用户没做任何操作时凭空消失。
    """
    run = await add_run(status="uploaded", billing_mode="credits", estimated_credits=40)
    try:
        await _switch(run.id, "credits")
        row = await read_run(run.id)
        assert row.estimated_credits == 40
        assert row.status == "uploaded"
    finally:
        await drop_run(run.id)


# --------------------------------------------------------------------------- #
# 再点一次「继续」：重置什么、拒绝什么
# --------------------------------------------------------------------------- #
async def test_schedule_state_clears_the_previous_submit_snapshot() -> None:
    """旧的 `submit_mode` 描述的是「上一次提交怎么发生的」。

    留着它，恢复器会按上一条记录的供应商形态去处置这一次；`retry_count` 同理：
    用户手动再点一次就是重新给一份重试额度，否则上一轮耗尽的记录会在刚入队时
    就被判「已重试 5 次」。
    """
    run = await add_run(
        status="service_error",
        framework_status="failed",
        submit_mode="async",
        retry_count=4,
        last_error="上传准备未完成",
        error_code="UPLOAD_INCOMPLETE",
    )
    try:
        assert await _schedule_state(run.id) == "prepare"
        row = await read_run(run.id)
        assert (row.status, row.framework_status) == ("queued", "queued")
        assert row.submit_mode is None
        assert row.retry_count == 0
        assert row.last_error is None
    finally:
        await drop_run(run.id)


async def test_schedule_state_never_reposts_unknown_receipts() -> None:
    """`submit_attempts>0` 且没有任务号 = 上一次可能已经送达，只能人工核对。"""
    run = await add_run(status="service_error", submit_attempts=1)
    try:
        with pytest.raises(Conflict) as raised:
            await _schedule_state(run.id)
        assert "提交回执未知" in str(raised.value)
        row = await read_run(run.id)
        assert row.status == "service_error", "拒绝时不能顺手把状态改掉"
        assert row.submit_attempts == 1
    finally:
        await drop_run(run.id)


async def test_schedule_state_polls_when_task_already_exists() -> None:
    """有任务号就只续查，不重新准备——重新准备会再提交一次。"""
    run = await add_run(status="waiting_executor", task_id="upstream-1")
    try:
        assert await _schedule_state(run.id) == "poll"
        assert (await read_run(run.id)).framework_status == "running"
    finally:
        await drop_run(run.id)


@pytest.mark.parametrize("status", ["preparing", "prepare_failed"])
async def test_schedule_state_refuses_unprepared_inputs(status: str) -> None:
    """输入还没落地就「继续提交」，执行器会在 upload 阶段 ENOENT。"""
    run = await add_run(status=status)
    try:
        with pytest.raises(Conflict) as raised:
            await _schedule_state(run.id)
        assert "尚未准备完成" in str(raised.value)
    finally:
        await drop_run(run.id)


async def test_schedule_state_answers_done_for_settled_runs() -> None:
    """已经成功的再点「继续」是空操作，不是错误——前端会轮询到它。"""
    done = await add_run(status="success")
    unknown = await add_run(status="cancelled")
    try:
        assert await _schedule_state(done.id) == "done"
        with pytest.raises(Conflict) as raised:
            await _schedule_state(unknown.id)
        assert "不能提交" in str(raised.value)
    finally:
        await drop_run(done.id)
        await drop_run(unknown.id)


# --------------------------------------------------------------------------- #
# 启动恢复：只推进还有事可做的行
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("fields", "expected"), [
    ({"status": "submitting"}, "done"),
    ({"status": "service_error", "submit_attempts": 2}, "done"),
])
async def test_recover_state_freezes_interrupted_submission(
    fields: dict[str, object], expected: str
) -> None:
    """断在提交中途的记录必须定格成「回执未知」，不能当作普通失败重来。"""
    run = await add_run(**fields)
    try:
        assert await _recover_state(run.id) == expected
        row = await read_run(run.id)
        assert row.status == "submission_unknown"
        assert row.framework_status == "failed"
        assert "禁止自动重提" in (row.last_error or "")
    finally:
        await drop_run(run.id)


@pytest.mark.parametrize(
    "status", ["queued", "uploading", "uploaded", "waiting_executor", "prepared"]
)
async def test_recover_state_requeues_unfinished_runs(status: str) -> None:
    run = await add_run(status=status)
    try:
        assert await _recover_state(run.id) == "prepare"
    finally:
        await drop_run(run.id)


@pytest.mark.parametrize("status", ["success", "failed", "service_error", "submission_unknown"])
async def test_recover_state_leaves_settled_runs_alone(status: str) -> None:
    """终态行一眼都不多看一眼：多看一眼就要多查一次上游，集合也就永不收敛。"""
    run = await add_run(status=status, submit_attempts=0)
    try:
        assert await _recover_state(run.id) == "done"
        assert (await read_run(run.id)).status == status
    finally:
        await drop_run(run.id)


async def test_recover_state_ignores_missing_rows() -> None:
    """行被清掉了还投来一个恢复作业：直接结束，不要 404 到日志里刷屏。"""
    assert await _recover_state("0" * 32) == "done"


async def test_recover_waits_for_run_lock_with_bounded_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """抢不到锁时退避重投，六轮之后放手，且**一个字都不写**。

    放弃时写一笔「猜测的状态」才是覆盖回执的起点：真正持锁的进程收尾时会自己
    把状态写对，它若崩了，下一轮恢复也能正常继续。
    """
    calls: list[tuple[str, float, int]] = []

    async def fake_enqueue(run_id: str, *, delay: float = 0, attempt: int = 0) -> None:
        calls.append((run_id, delay, attempt))

    monkeypatch.setattr(worker, "enqueue_recovery", fake_enqueue)
    run_id = "a" * 32

    await worker._wait_for_release(run_id, 0)
    assert calls == [(run_id, 30.0, 1)]

    calls.clear()
    await worker._wait_for_release(run_id, 5)
    assert calls == [(run_id, 600.0, 6)], "退避必须封顶，否则越等越久"

    calls.clear()
    giveups = worker.RECOVERY_LOCK_GIVEUPS.value()
    await worker._wait_for_release(run_id, worker.RECOVER_LOCK_MAX_RETRIES)
    assert calls == [], "轮次用完就放手，不能再投"
    assert worker.RECOVERY_LOCK_GIVEUPS.value() == giveups + 1


async def test_resume_converges_and_skips_settled_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启动扫描只挑「还有事可做」的行，终态与等人确认的一律跳过。"""
    unfinished = await add_run(status="queued")
    uploading = await add_run(status="uploading")
    interrupted = await add_run(status="submitting")
    settled = [
        await add_run(status="success"),
        await add_run(status="failed", task_id="upstream-9"),
        await add_run(status="service_error"),
        await add_run(status="submission_unknown", submit_attempts=1),
    ]
    queued: list[str] = []

    async def fake_enqueue(run_id: str, *, delay: float = 0, attempt: int = 0) -> None:
        queued.append(run_id)

    monkeypatch.setattr(settings, "worker_enabled", True)
    monkeypatch.setattr(worker, "enqueue_recovery", fake_enqueue)
    try:
        await worker.resume()
        assert {unfinished.id, uploading.id, interrupted.id} <= set(queued)
        assert not {row.id for row in settled} & set(queued)
    finally:
        for row in [unfinished, uploading, interrupted, *settled]:
            await drop_run(row.id)


async def test_resume_does_nothing_when_worker_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本实例不消费队列时不该往队列里投东西：投了也没人做，只会积压。"""
    run = await add_run(status="queued")
    queued: list[str] = []

    async def fake_enqueue(run_id: str, *, delay: float = 0, attempt: int = 0) -> None:
        queued.append(run_id)

    assert settings.worker_enabled is False, "本用例前提是 conftest 关掉了 worker"
    monkeypatch.setattr(worker, "enqueue_recovery", fake_enqueue)
    try:
        await worker.resume()
        assert run.id not in queued
    finally:
        await drop_run(run.id)


async def test_resume_waits_when_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """数据库不可用是「稍后再扫」，不是启动失败。

    启动路径上抛异常会让整个进程起不来——而恢复恰恰是数据库刚恢复时最该跑的东西。
    """

    async def boom(self, statuses):  # noqa: ANN001, ANN202 - 替身只关心被调用
        raise UpstreamError("共享盘暂不可用")

    queued: list[str] = []

    async def fake_enqueue(run_id: str, *, delay: float = 0, attempt: int = 0) -> None:
        queued.append(run_id)

    monkeypatch.setattr(settings, "worker_enabled", True)
    monkeypatch.setattr(RunRepository, "list_unfinished", boom)
    monkeypatch.setattr(worker, "enqueue_recovery", fake_enqueue)

    await worker.resume()
    assert queued == []


# --------------------------------------------------------------------------- #
# 流水线回写：失败要按「有没有任务号」分类
# --------------------------------------------------------------------------- #
async def test_mark_failed_classifies_by_receipt_and_status() -> None:
    """框架只说「失败了」，落到业务上要分成四种完全不同的处置。

    - 已尝试提交但没有任务号 → `submission_unknown`（可能已经花了钱）
    - `waiting_free` → 保持不动（它在等权益，不是失败）
    - 执行器被占用 → `waiting_executor`（等一会儿再来，不算用户的错）
    - 其余 → 有任务号记 `failed`，没有记 `service_error`
    """
    unknown = await add_run(status="uploading", submit_attempts=1)
    waiting = await add_run(status="waiting_free")
    busy = await add_run(status="uploading")
    failed = await add_run(status="running", task_id="upstream-2")
    broken = await add_run(status="uploading")
    try:
        for row in (unknown, waiting, busy, failed, broken):
            await _alive(row.id)
        async with _session() as session:
            store = ReplacementStore(session)
            await store.mark_failed(unknown.id, "提交中断")
            await store.mark_failed(waiting.id, "上游 500")
            await store.mark_failed(busy.id, "执行器被占用，稍后重试")
            await store.mark_failed(failed.id, "上游 500")
            await store.mark_failed(broken.id, "上传失败")

        assert (await read_run(unknown.id)).status == "submission_unknown"
        assert (await read_run(waiting.id)).status == "waiting_free"
        assert (await read_run(busy.id)).status == "waiting_executor"
        assert (await read_run(failed.id)).status == "failed"
        assert (await read_run(broken.id)).status == "service_error"
    finally:
        for row in (unknown, waiting, busy, failed, broken):
            await drop_run(row.id)
