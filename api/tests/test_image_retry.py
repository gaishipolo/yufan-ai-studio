"""R11-02：提交结果未知的手动重试出口 + 终态保护 + 巡检下推。

`SUBMIT_UNKNOWN_MESSAGE` 写着「请核对后手动重试」，但修复前 `image` 没有
retry 出口：runner 判死后 `handle_submit` 的终态早退让记录再也动不了。
这里既测出口本身，也测「死锁确实解除了」（`record.submit_unknown` 为 False）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from db.audit import AuditEvent
from db.session import get_engine, get_session_factory
from generation.runner import GenerationRunner
from generation.spec import STATUS_FAILED, STATUS_QUEUED
from infra.ai.base import GenerationProgress, GenerationState
from infra.queue.base import Job
from infra.queue.memory import MemoryJobQueue
from modules.image.models import ImageJob
from modules.image.pipeline import ImageJobStore

API = "/api/v1"
_OLD = datetime.now(tz=UTC) - timedelta(hours=2)


async def _insert(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "name": "r11-probe",
        "prompt": "不该被重试响应泄露的提示词",
        "status": STATUS_QUEUED,
        "owner_id": "unknown",
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


@pytest.fixture
def created_ids() -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    if ids:
        asyncio.run(_delete(ids))


def _me_id(client: TestClient) -> str:
    resp = client.get(f"{API}/identity/me")
    assert resp.status_code == 200
    return str(resp.json()["data"]["id"])


async def test_retry_requeues_failed_job_and_clears_submit_intent(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _me_id(client)
    rid = await _insert(
        owner_id=actor,
        status=STATUS_FAILED,
        submit_attempts=1,
        error_message="提交结果未知：请核对后手动重试",
    )
    created_ids.append(rid)

    queue = MemoryJobQueue()
    monkeypatch.setattr(
        "modules.image.service.get_runner", lambda: GenerationRunner(queue)
    )

    resp = client.post(f"{API}/images/{rid}/retry")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "queued"
    assert set(data) == {"id", "status"}, "重试响应不该带别人的 prompt"

    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        assert job.status == STATUS_QUEUED
        assert job.submit_attempts == 0
        assert job.external_id is None
        assert job.error_message is None
        assert job.finished_at is None
        # 关键：不带「提交结果未知」形状回到队列，否则 runner 会立刻再判死
        record = await ImageJobStore(session).load(rid)
        assert record is not None and not record.submit_unknown

    assert any(j["name"] == "generation.submit" for j in queue.snapshot())


async def test_retry_rejects_non_failed_state(
    client: TestClient, created_ids: list[str]
) -> None:
    actor = _me_id(client)
    rid = await _insert(owner_id=actor, status=STATUS_QUEUED)
    created_ids.append(rid)

    resp = client.post(f"{API}/images/{rid}/retry")
    assert resp.status_code == 409


async def test_retry_of_others_requires_retry_any(
    client: TestClient, created_ids: list[str]
) -> None:
    """匿名演示账号带 `*`，HTTP 层测不出这个闸门，直接测 service。"""
    from core.exceptions import Forbidden
    from modules.image.service import ImageService

    rid = await _insert(owner_id="someone-else", status=STATUS_FAILED)
    created_ids.append(rid)

    class _Actor:
        id = "me"

        def has(self, code: str) -> bool:
            return False

    async with get_session_factory()() as session:
        with pytest.raises(Forbidden):
            await ImageService(session).retry(_Actor(), rid)  # type: ignore[arg-type]


async def test_retry_of_missing_job_is_404(client: TestClient) -> None:
    assert client.post(f"{API}/images/does-not-exist/retry").status_code == 404


async def test_store_never_overwrites_a_terminal_record(
    client: TestClient, created_ids: list[str]
) -> None:
    """并发下迟到的提交/进度/成功回调，不许把已判死的记录覆盖回来。"""
    actor = _me_id(client)
    rid = await _insert(owner_id=actor, status=STATUS_FAILED, submit_attempts=1)
    created_ids.append(rid)

    async with get_session_factory()() as session:
        store = ImageJobStore(session)
        await store.mark_submitted(rid, "ext-late")
        await store.mark_progress(rid, 55)
        await store.mark_succeeded(rid, output_url="/static/x.png", cover_url=None)
        await session.commit()

    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        assert job.status == STATUS_FAILED
        assert job.external_id is None
        assert job.output_url is None


async def test_settle_skips_terminal_record_without_emitting_success(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """框架层第二道保护：不发出「先失败后成功」的乱序事件。"""
    actor = _me_id(client)
    rid = await _insert(owner_id=actor, status=STATUS_FAILED)
    created_ids.append(rid)

    events: list[str] = []

    class _Bus:
        async def publish(self, name: str, **payload: object) -> None:
            events.append(name)

    monkeypatch.setattr("generation.runner.get_event_bus", lambda: _Bus())
    result = GenerationProgress(
        external_id="late",
        state=GenerationState.SUCCEEDED,
        progress=100,
        output_url="https://cdn.example.com/a.png",
    )
    async with get_session_factory()() as session:
        await GenerationRunner(MemoryJobQueue())._settle(
            store=ImageJobStore(session),
            session=session,
            kind="image",
            record_id=rid,
            owner_id=actor,
            model="echo-image",
            result=result,
        )
    assert events == []


def test_stale_threshold_covers_the_provider_timeout() -> None:
    """90 秒 < Agnes 180 秒是配置级误伤窗口，阈值必须由供应商超时推导。"""
    from infra.ai.agnes import AgnesSettings
    from modules.image.maintenance import _stale_after_sec

    assert _stale_after_sec() >= AgnesSettings().timeout_sec * 1.5


async def test_reconcile_pushes_cutoff_and_limit_into_sql(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from modules.image.maintenance import BATCH, reconcile_stale_jobs

    dispatched: list[str] = []

    class _Runner:
        async def dispatch(self, kind: str, rid: str) -> str:
            dispatched.append(rid)
            return "ok"

    monkeypatch.setattr("modules.image.maintenance.get_runner", lambda: _Runner())
    for i in range(BATCH + 3):
        created_ids.append(await _insert(name=f"stale-{i}"))

    sqls: list[str] = []
    engine = get_engine().sync_engine

    def capture(
        conn: object,
        cursor: object,
        statement: str,
        params: object,
        context: object,
        executemany: bool,
    ) -> None:
        if "FROM image_jobs" in statement:
            sqls.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        await reconcile_stale_jobs()
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert 0 < len(dispatched) <= BATCH, "BATCH 必须真的限制投递条数"
    assert any("LIMIT" in sql.upper() for sql in sqls), "LIMIT 必须下推到 SQL，不能先全表加载"
    assert any("coalesce" in sql.lower() for sql in sqls), "应按提交开始时间判陈旧"


async def test_reconcile_logs_review_branch_separately(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """日志说「重新投递」但结果是把记录判死，运维会被误导（R11-02 现象 B）。"""
    from modules.image.maintenance import reconcile_stale_jobs

    class _Runner:
        async def dispatch(self, kind: str, rid: str) -> str:
            return "ok"

    monkeypatch.setattr("modules.image.maintenance.get_runner", lambda: _Runner())
    created_ids.append(await _insert(submit_attempts=1, started_at=_OLD))

    with caplog.at_level("WARNING"):
        await reconcile_stale_jobs()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "待人工核对" in messages
    assert "重新投递" not in messages or "提交结果未知" in messages


# --------------------------------------------------------------------------- #
# R12-01：两种 failed 不能共用一个出口
# --------------------------------------------------------------------------- #
async def test_retry_of_charged_job_requires_explicit_confirmation(
    client: TestClient, created_ids: list[str]
) -> None:
    """恢复器把同步孤儿置成 failed「请重试」，但一次点击不能变成第二次计费。"""
    actor = _me_id(client)
    rid = await _insert(
        owner_id=actor,
        status=STATUS_FAILED,
        external_id="agnes-task-REAL-123",
        error_message="生成结果未能落库：该供应商是同步接口，不支持续查",
    )
    created_ids.append(rid)

    resp = client.post(f"{API}/images/{rid}/retry")
    assert resp.status_code == 409
    assert "可能已产生费用" in resp.json()["message"]

    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        assert job.status == STATUS_FAILED, "拒绝时不能动状态"
        assert job.external_id == "agnes-task-REAL-123", "拒绝时不能动对账凭证"


async def test_confirmed_retry_keeps_reconciliation_evidence(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式确认后可以重投，但旧任务号必须留在记录与审计两处。"""
    actor = _me_id(client)
    rid = await _insert(
        owner_id=actor, status=STATUS_FAILED, external_id="agnes-task-REAL-123"
    )
    created_ids.append(rid)
    queue = MemoryJobQueue()
    monkeypatch.setattr(
        "modules.image.service.get_runner", lambda: GenerationRunner(queue)
    )

    resp = client.post(f"{API}/images/{rid}/retry?confirmCharged=true")
    assert resp.status_code == 200, resp.text

    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        assert job.status == STATUS_QUEUED
        assert job.external_id is None
        assert job.previous_external_id == "agnes-task-REAL-123"
        assert job.submit_attempts == 0
        rows = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.target_id == rid)
            )
        ).all()
    assert any("agnes-task-REAL-123" in (row.detail or "") for row in rows)
    assert any(j["name"] == "generation.submit" for j in queue.snapshot())


# --------------------------------------------------------------------------- #
# R12-02：_guard 的 refresh 不许冲掉本会话未提交的清标记
# --------------------------------------------------------------------------- #
async def test_connect_error_clears_submit_attempt(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """确定没送达 → 清标记 → 失败：三步在同一 session 里，attempts 必须归零。"""
    import generation.runner as gr

    actor = _me_id(client)
    rid = await _insert(owner_id=actor)
    created_ids.append(rid)

    class _Provider:
        async def submit(self, request: object) -> str:
            raise httpx.ConnectError("模拟连不上")

    class _Router:
        def provider_for(self, model: str) -> _Provider:
            return _Provider()

    monkeypatch.setattr(gr, "get_model_router", lambda: _Router())
    await gr.GenerationRunner(MemoryJobQueue()).handle_submit(
        Job(
            name=gr.JOB_SUBMIT,
            payload={"kind": "image", "record_id": rid},
            idempotency_key=f"submit:image:{rid}",
            max_attempts=1,
        )
    )

    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        assert job.status == STATUS_FAILED
        assert job.submit_attempts == 0, "确定没送达不该被算成「可能已计费」"
        assert job.external_id is None


# --------------------------------------------------------------------------- #
# R13-02：凭证是追加语义，不是单槽
# --------------------------------------------------------------------------- #
async def test_confirmed_retry_appends_evidence_instead_of_overwriting(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """连续两次「确认已扣费」重试，第一份账单的凭证不许被第二次覆盖。

    单槽 `previous_external_id` 的价值就在「别把对账凭证弄丢」，而它只能存一个；
    审计 detail 按设计吞异常，所以两份凭证会在同一轮里一起丢（R13-02）。
    """
    actor = _me_id(client)
    rid = await _insert(
        owner_id=actor, status=STATUS_FAILED, external_id="EXT-FIRST"
    )
    created_ids.append(rid)
    monkeypatch.setattr(
        "modules.image.service.get_runner", lambda: GenerationRunner(MemoryJobQueue())
    )

    first = client.post(f"{API}/images/{rid}/retry?confirmCharged=true")
    assert first.status_code == 200, first.text

    # 第二次提交又拿到任务号并再次失败（同步供应商出图后落库失败的现场）
    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        job.status = STATUS_FAILED
        job.external_id = "EXT-SECOND"
        await session.commit()

    second = client.post(f"{API}/images/{rid}/retry?confirmCharged=true")
    assert second.status_code == 200, second.text

    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        assert job.previous_external_id == "EXT-SECOND", "单槽仍返回最近一个"
        assert job.previous_external_ids == ["EXT-FIRST", "EXT-SECOND"], (
            "第一份账单的凭证被覆盖了：这就是 R13-02"
        )

    detail = client.get(f"{API}/images?page=1&pageSize=50")
    assert detail.status_code == 200


# --------------------------------------------------------------------------- #
# R13-04：巡检按 submit_mode 分流，有任务号的只 resume 不 dispatch
# --------------------------------------------------------------------------- #
async def test_reconcile_resumes_accepted_jobs_and_never_dispatches(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`external_id is None` 一刀切会把最需要接管的记录全挡在外面（R13-04）。

    轮询链在两次进程重启之间断掉，启动恢复器（每次启动一次、上限 100 条）
    管不到，只能靠巡检续接；而重投这类记录就是第二次提交、第二次计费。
    """
    from modules.image.maintenance import reconcile_stale_jobs

    dispatched: list[str] = []
    resumed: list[str] = []

    class _Runner:
        async def dispatch(self, kind: str, rid: str) -> str:
            dispatched.append(rid)
            return "ok"

        async def resume(self, kind: str, rid: str, *, delay: float = 0) -> None:
            resumed.append(rid)

    monkeypatch.setattr("modules.image.maintenance.get_runner", lambda: _Runner())

    async_mode = await _insert(
        status="running",
        external_id="EXT-ACCEPTED",
        submit_mode="async",
        started_at=_OLD,
    )
    sync_mode = await _insert(
        status="running",
        external_id="EXT-SYNC",
        submit_mode="sync",
        started_at=_OLD,
    )
    created_ids.extend([async_mode, sync_mode])

    await reconcile_stale_jobs()

    assert async_mode in resumed, "轮询链断掉的 running 记录没人接管"
    assert async_mode not in dispatched, "有任务号还 dispatch = 第二次提交（计费红线）"
    assert sync_mode not in resumed and sync_mode not in dispatched, (
        "同步供应商既续查不了也不许重投，应留给人工核对"
    )


# --------------------------------------------------------------------------- #
# R13-07：复位后投递失败，不能只抛 500 把现场留成「已复位但没入队」
# --------------------------------------------------------------------------- #
async def test_dispatch_failure_after_reset_is_explained(
    client: TestClient, created_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _me_id(client)
    rid = await _insert(owner_id=actor, status=STATUS_FAILED, submit_attempts=1)
    created_ids.append(rid)

    class _BrokenRunner:
        async def dispatch(self, kind: str, rid: str) -> str:
            raise RuntimeError("队列不可用")

    monkeypatch.setattr(
        "modules.image.service.get_runner", lambda: _BrokenRunner()
    )

    resp = client.post(f"{API}/images/{rid}/retry")
    assert resp.status_code == 502, resp.text
    assert "自动重投" in resp.json()["message"], "要告诉用户不用再点一次"

    async with get_session_factory()() as session:
        job = await session.get(ImageJob, rid)
        assert job is not None
        # 已复位成「未投递」形态：巡检会接手，重试按钮已不再生效
        assert job.status == STATUS_QUEUED
        assert job.submit_attempts == 0
