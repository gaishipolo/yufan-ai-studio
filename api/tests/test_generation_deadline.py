"""排队顺延：上游说「还在排队」时，不该把这段时间算进生成预算。

2026-11-17 的任务 29dc66ae：免费队列排了 29 分钟，第 30 分钟被判「生成超时」，
而上游一直在正常排队。这里钉住三条：
1. 排队时顺延墙钟起点；
2. 顺延有硬上限（按**建单时间**算，不是按挪过的起点）；
3. 不支持顺延的 store 不该被调用（协议探测，不是 getattr）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from generation.pipeline import DeadlineExtendable
from generation.runner import GenerationRunner
from generation.spec import GenerationRecord
from infra.queue.memory import MemoryJobQueue


class _Store:
    def __init__(self) -> None:
        self.extended = 0

    async def extend_deadline(self, record_id: str) -> bool:
        self.extended += 1
        return True


class _PlainStore:
    """没有顺延能力的实现：不该被调用，也不该报错。"""


def _record(*, created_minutes_ago: int) -> GenerationRecord:
    created = datetime.now(tz=UTC) - timedelta(minutes=created_minutes_ago)
    return GenerationRecord(
        id="a" * 32,
        kind="video",
        model="libtv-wan3-replacement",
        prompt="p",
        status="running",
        owner_id="u_001",
        created_at=created,
        timeout_started_at=created,
    )


def _extend(store: object, record: GenerationRecord) -> None:
    runner = GenerationRunner(MemoryJobQueue())
    asyncio.run(runner._extend_deadline(store, record, "video", record.id))  # noqa: SLF001


def test_queueing_extends_the_wall_clock() -> None:
    store = _Store()
    assert isinstance(store, DeadlineExtendable)

    _extend(store, _record(created_minutes_ago=5))

    assert store.extended == 1


def test_hard_cap_stops_endless_queueing() -> None:
    """排到硬上限（默认 3 小时）就停止顺延：永远排不上的任务必须有个尽头。"""
    store = _Store()

    _extend(store, _record(created_minutes_ago=4 * 60))

    assert store.extended == 0


def test_store_without_the_capability_is_never_called() -> None:
    """调用点靠 `isinstance` 探测（约定见 HANDOFF §5.5），没有这个方法就不该被调用。"""
    store = _PlainStore()

    assert not isinstance(store, DeadlineExtendable)


def test_queued_poll_does_not_burn_max_polls_budget() -> None:
    """排队等待不应消耗 `generation_max_polls`，否则等 15+ 分钟后会误判超时。"""
    from dataclasses import replace
    from types import SimpleNamespace

    from infra.ai.base import GenerationProgress, GenerationState
    from infra.queue.base import Job

    runner = GenerationRunner(MemoryJobQueue())
    scheduled: list[tuple[str, str, int, float]] = []

    async def fake_schedule(kind: str, record_id: str, *, polls: int, delay: float) -> None:
        scheduled.append((kind, record_id, polls, delay))

    class _FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    class _FakeStore:
        async def load(self, record_id: str) -> GenerationRecord:
            return replace(_record(created_minutes_ago=5), external_id=f"{record_id}:upstream")

        async def extend_deadline(self, record_id: str) -> bool:
            return True

        async def mark_progress(self, record_id: str, progress: int) -> bool:
            return True

    class _FakeProvider:
        poll_interval_sec = 5.0

        async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.RUNNING,
                queued=True,
                progress=9,
            )

    import generation.runner as runner_mod

    orig_factory = runner_mod.get_session_factory
    orig_pipeline = runner_mod.get_pipeline
    orig_provider_for = runner_mod._provider_for
    runner_mod.get_session_factory = lambda: lambda: _FakeSession()  # type: ignore[assignment]
    runner_mod.get_pipeline = lambda kind: SimpleNamespace(store=lambda s: _FakeStore())  # type: ignore[assignment]
    runner_mod._provider_for = lambda pipeline, model: _FakeProvider()  # type: ignore[assignment]
    runner._schedule_poll = fake_schedule  # type: ignore[method-assign]
    try:
        asyncio.run(
            runner.handle_poll(
                Job(
                    name="generation.poll",
                    payload={"kind": "video", "record_id": "a" * 32, "polls": 7},
                )
            )
        )
    finally:
        runner_mod.get_session_factory = orig_factory
        runner_mod.get_pipeline = orig_pipeline
        runner_mod._provider_for = orig_provider_for

    assert scheduled == [("video", "a" * 32, 7, 15.0)]


def test_queued_poll_commits_deadline_extension_even_when_progress_unchanged() -> None:
    """排队期间进度常停在同一百分比，必须独立提交 started_at 顺延，
    不能靠 mark_progress 顺带提交。"""
    from dataclasses import replace
    from types import SimpleNamespace

    from infra.ai.base import GenerationProgress, GenerationState
    from infra.queue.base import Job

    runner = GenerationRunner(MemoryJobQueue())
    commits = 0
    extended = 0

    async def fake_schedule(kind: str, record_id: str, *, polls: int, delay: float) -> None:
        return None

    class _FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    class _FakeStore:
        async def load(self, record_id: str) -> GenerationRecord:
            return replace(
                _record(created_minutes_ago=5),
                progress=48,
                external_id=f"{record_id}:upstream",
            )

        async def extend_deadline(self, record_id: str) -> bool:
            nonlocal extended
            extended += 1
            return True

        async def mark_progress(self, record_id: str, progress: int) -> bool:
            raise AssertionError("进度未变化时不该调用 mark_progress")

    class _FakeProvider:
        poll_interval_sec = 5.0

        async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.RUNNING,
                queued=True,
                progress=48,
            )

    import generation.runner as runner_mod

    orig_factory = runner_mod.get_session_factory
    orig_pipeline = runner_mod.get_pipeline
    orig_provider_for = runner_mod._provider_for
    runner_mod.get_session_factory = lambda: lambda: _FakeSession()  # type: ignore[assignment]
    runner_mod.get_pipeline = lambda kind: SimpleNamespace(store=lambda s: _FakeStore())  # type: ignore[assignment]
    runner_mod._provider_for = lambda pipeline, model: _FakeProvider()  # type: ignore[assignment]
    runner._schedule_poll = fake_schedule  # type: ignore[method-assign]
    try:
        asyncio.run(
            runner.handle_poll(
                Job(
                    name="generation.poll",
                    payload={"kind": "video", "record_id": "a" * 32, "polls": 3},
                )
            )
        )
    finally:
        runner_mod.get_session_factory = orig_factory
        runner_mod.get_pipeline = orig_pipeline
        runner_mod._provider_for = orig_provider_for

    assert extended == 1
    assert commits == 1


def test_queued_poll_persists_started_at_with_real_db_store() -> None:
    """使用真实 AsyncSession + VideoTaskStore，验证进度不变的排队轮询确实推进了
    库里的 started_at。"""
    import uuid

    import generation.runner as runner_mod
    from db.session import get_session_factory
    from infra.ai.base import GenerationProgress, GenerationState
    from infra.queue.base import Job
    from modules.task.models import VideoTask

    task_id = uuid.uuid4().hex
    # 只比 `generation_timeout_sec`（默认 1800s）小一点点，别贴着边界：这条用例要的是
    # 「起点被改写了」，不该因为有人调小默认超时而开始走超时分支。
    old_started = datetime.now(tz=UTC) - timedelta(minutes=2)

    async def seed() -> None:
        async with get_session_factory()() as session:
            session.add(
                VideoTask(
                    id=task_id,
                    title="queued-deadline-persist",
                    prompt="p",
                    model="libtv-wan3-replacement",
                    model_id="libtv-wan3-replacement",
                    status="running",
                    progress=48,
                    author_id="test-user",
                    external_id=f"{task_id}:upstream",
                    started_at=old_started,
                )
            )
            await session.commit()

    async def fetch_started() -> datetime | None:
        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            return row.started_at if row is not None else None

    async def cleanup() -> None:
        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            if row is not None:
                await session.delete(row)
                await session.commit()

    class _FakeProvider:
        poll_interval_sec = 5.0

        async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.RUNNING,
                queued=True,
                progress=48,
            )

    runner = GenerationRunner(MemoryJobQueue())

    async def fake_schedule(kind: str, record_id: str, *, polls: int, delay: float) -> None:
        return None

    orig_provider_for = runner_mod._provider_for
    runner_mod._provider_for = lambda pipeline, model: _FakeProvider()  # type: ignore[assignment]
    runner._schedule_poll = fake_schedule  # type: ignore[method-assign]
    asyncio.run(seed())
    try:
        asyncio.run(
            runner.handle_poll(
                Job(
                    name="generation.poll",
                    payload={"kind": "video", "record_id": task_id, "polls": 1},
                )
            )
        )
        updated = asyncio.run(fetch_started())
    finally:
        runner_mod._provider_for = orig_provider_for
        asyncio.run(cleanup())

    assert updated is not None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    assert updated > old_started
