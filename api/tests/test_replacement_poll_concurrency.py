"""Cross-instance poll results must not overwrite a newer execution or terminal state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from tests_template_helpers import add_run, drop_run, patch_run, read_run

from db.session import get_session_factory
from generation import runner as runner_module
from generation.runner import JOB_POLL, GenerationRunner
from infra.ai.base import GenerationProgress, GenerationState
from infra.queue.base import Job
from infra.queue.memory import MemoryJobQueue
from modules.template import worker
from modules.template.models import ReplacementRun
from modules.template.pipeline import KIND_REPLACEMENT, ReplacementPipeline, ReplacementStore
from workers import HANDLERS, Worker


def _job(run_id: str, **kwargs) -> Job:
    return Job(
        name=JOB_POLL,
        payload={"kind": KIND_REPLACEMENT, "record_id": run_id},
        **kwargs,
    )


@pytest.mark.parametrize("late_state", [GenerationState.FAILED, GenerationState.SUCCEEDED])
async def test_late_poll_cannot_replace_another_terminal_result(monkeypatch, late_state):
    run = await add_run(status="running", framework_status="running", task_id="paid-task")
    entered, release = asyncio.Event(), asyncio.Event()
    first_state = (
        GenerationState.SUCCEEDED
        if late_state == GenerationState.FAILED
        else GenerationState.FAILED
    )

    class Provider:
        async def poll(self, external_id, **kwargs):
            state = first_state
            if asyncio.current_task().get_name() == "late-poll":
                entered.set()
                await release.wait()
                state = late_state
            return GenerationProgress(external_id, state, 100, error="terminal failure")

    monkeypatch.setattr(ReplacementPipeline, "resolve_provider", lambda *args: Provider())
    monkeypatch.setattr(worker, "sync_projection", lambda run: True)
    events = []
    runner = GenerationRunner(MemoryJobQueue())

    async def emit(name, **payload):
        events.append(name)

    monkeypatch.setattr(runner, "_emit", emit)
    late = asyncio.create_task(runner.handle_poll(_job(run.id)), name="late-poll")
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await runner.handle_poll(_job(run.id))
        release.set()
        await late
        saved = await read_run(run.id)
        expected = "success" if first_state == GenerationState.SUCCEEDED else "failed"
        assert saved.status == saved.framework_status == expected
        assert events == [f"generation.{'succeeded' if expected == 'success' else 'failed'}"]
    finally:
        release.set()
        await late
        await drop_run(run.id)


async def test_poll_from_previous_execution_cannot_fail_manual_resume(monkeypatch):
    started = datetime.now(UTC) - timedelta(seconds=5)
    run = await add_run(
        status="running", framework_status="running", task_id="paid-task", started_at=started
    )
    entered, release = asyncio.Event(), asyncio.Event()

    class Provider:
        async def poll(self, external_id, **kwargs):
            entered.set()
            await release.wait()
            return GenerationProgress(external_id, GenerationState.FAILED, error="old execution")

    monkeypatch.setattr(ReplacementPipeline, "resolve_provider", lambda *args: Provider())
    monkeypatch.setattr(worker, "sync_projection", lambda run: True)
    runner = GenerationRunner(MemoryJobQueue())
    events = []

    async def emit(name, **payload):
        events.append(name)

    monkeypatch.setattr(runner, "_emit", emit)
    late = asyncio.create_task(runner.handle_poll(_job(run.id)))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await patch_run(run.id, started_at=datetime.now(UTC))
        release.set()
        await late
        saved = await read_run(run.id)
        assert saved.status == saved.framework_status == "running"
        assert saved.last_error is None
        assert events == []
    finally:
        release.set()
        await late
        await drop_run(run.id)


async def test_poll_timeout_leaves_a_scheduled_successor(monkeypatch):
    run = await add_run(status="running", framework_status="running", task_id="paid-task")

    class Provider:
        async def poll(self, external_id, **kwargs):
            await asyncio.sleep(1)
            return GenerationProgress(external_id, GenerationState.RUNNING)

    monkeypatch.setattr(ReplacementPipeline, "resolve_provider", lambda *args: Provider())
    monkeypatch.setattr(worker, "sync_projection", lambda run: True)
    monkeypatch.setattr(runner_module.settings, "generation_poll_interval_sec", 0.001)
    queue = MemoryJobQueue()
    runner = GenerationRunner(queue)
    monkeypatch.setitem(HANDLERS, JOB_POLL, runner.handle_poll)
    await queue.enqueue(_job(run.id, timeout_sec=0.1, max_attempts=1))
    consumer = Worker(queue)
    try:
        for _ in range(2):
            job = await queue.dequeue(timeout=3)
            assert job is not None
            # Keep the test at the same budget for successor jobs too.
            job.timeout_sec = 0.1
            await consumer._handle(0, job)
        successor = await queue.dequeue(timeout=3)
        assert successor is not None, "poll timeout exhausted queue attempts without a successor"
        assert successor.payload["polls"] == 2
        saved = await read_run(run.id)
        assert saved.status == saved.framework_status == "running"
    finally:
        await drop_run(run.id)


@pytest.mark.parametrize("progress", [100, 50])
async def test_clamped_or_older_progress_keeps_polling(monkeypatch, progress):
    run = await add_run(
        status="running", framework_status="running", task_id="paid-task", progress_percent=99
    )

    class Provider:
        async def poll(self, external_id, **kwargs):
            return GenerationProgress(external_id, GenerationState.RUNNING, progress)

    monkeypatch.setattr(ReplacementPipeline, "resolve_provider", lambda *args: Provider())
    monkeypatch.setattr(worker, "sync_projection", lambda run: True)
    queue = MemoryJobQueue()
    runner = GenerationRunner(queue)
    events = []

    async def emit(name, **payload):
        events.append(name)

    monkeypatch.setattr(runner, "_emit", emit)
    try:
        await runner.handle_poll(_job(run.id))
        assert await queue.size() == 1
        assert (await read_run(run.id)).progress_percent == 99
        assert events == []
    finally:
        await drop_run(run.id)


async def test_post_commit_refresh_cannot_adopt_another_execution(monkeypatch):
    started = datetime.now(UTC)
    run = await add_run(status="uploaded", framework_status="queued", started_at=started)
    monkeypatch.setattr(worker, "sync_projection", lambda run: True)
    try:
        async with get_session_factory()() as session:
            store = ReplacementStore(session)
            await store.load(run.id)
            refresh = session.refresh

            async def resume_before_refresh(instance, *args, **kwargs):
                await patch_run(run.id, started_at=started + timedelta(seconds=1))
                await refresh(instance, *args, **kwargs)

            monkeypatch.setattr(session, "refresh", resume_before_refresh)
            assert await store.mark_submitted(run.id, f"{run.id}:paid-task") is True
            assert await store.mark_failed(run.id, "old execution") is False
        assert (await read_run(run.id)).framework_status == "running"
    finally:
        await drop_run(run.id)


async def test_only_one_loaded_submitter_can_claim_submission(monkeypatch):
    run = await add_run(status="uploaded", framework_status="queued")
    monkeypatch.setattr(worker, "sync_projection", lambda run: True)
    try:
        async with get_session_factory()() as first, get_session_factory()() as second:
            a, b = ReplacementStore(first), ReplacementStore(second)
            await a.load(run.id)
            await b.load(run.id)
            assert await a.mark_submitting(run.id) is True
            assert await b.mark_submitting(run.id) is False
    finally:
        await drop_run(run.id)


async def test_stale_orm_writer_cannot_replace_newer_state():
    from sqlalchemy.orm.exc import StaleDataError

    run = await add_run(status="uploaded", framework_status="queued")
    try:
        async with get_session_factory()() as first, get_session_factory()() as second:
            current = await first.get(ReplacementRun, run.id)
            stale = await second.get(ReplacementRun, run.id)
            current.last_error = "newer state"
            await first.commit()
            stale.status = "failed"
            with pytest.raises(StaleDataError):
                await second.commit()
            await second.rollback()
    finally:
        await drop_run(run.id)


async def test_metadata_write_during_poll_settlement_does_not_drop_successor(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    run = await add_run(status="running", framework_status="running", task_id="paid-task")

    class Provider:
        async def poll(self, external_id, **kwargs):
            return GenerationProgress(external_id, GenerationState.RUNNING, 25)

    monkeypatch.setattr(ReplacementPipeline, "resolve_provider", lambda *args: Provider())
    monkeypatch.setattr(worker, "sync_projection", lambda run: True)
    execute = AsyncSession.execute
    changed = False

    async def metadata_before_update(session, statement, *args, **kwargs):
        nonlocal changed
        if getattr(statement, "is_update", False) and not changed:
            changed = True
            await patch_run(run.id, credits=20)
        return await execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", metadata_before_update)
    queue = MemoryJobQueue()
    try:
        await GenerationRunner(queue).handle_poll(_job(run.id))
        saved = await read_run(run.id)
        assert await queue.size() == 1, "a concurrent billing write must not end the poll chain"
        assert saved.progress_percent == 25
        assert saved.credits == 20
    finally:
        await drop_run(run.id)
