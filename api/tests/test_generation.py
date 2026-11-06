"""生成框架与扩展底座的行为测试。

覆盖三件容易被写错的事：延迟投递、幂等、订阅者失败隔离。
"""

from __future__ import annotations

import asyncio

import pytest

from core.events import EventBus
from core.exceptions import BadRequest, Forbidden, UpstreamError
from core.registry import NamedRegistry, RegistryError
from generation.pipeline import register_pipeline
from generation.runner import JOB_POLL, JOB_SUBMIT, GenerationRunner
from infra.ai.base import Capability, GenerationProgress, GenerationState
from infra.queue.base import Job
from infra.queue.memory import MemoryJobQueue


# --------------------------------------------------------------------------- #
# 队列
# --------------------------------------------------------------------------- #
def test_delayed_job_is_not_ready_immediately() -> None:
    async def scenario() -> Job | None:
        queue = MemoryJobQueue()
        await queue.enqueue(Job(name="poll", payload={}), delay=30)
        return await queue.dequeue(timeout=0.1)

    assert asyncio.run(scenario()) is None


def test_delayed_job_becomes_ready() -> None:
    async def scenario() -> Job | None:
        queue = MemoryJobQueue()
        await queue.enqueue(Job(name="poll", payload={"n": 1}), delay=0.2)
        return await queue.dequeue(timeout=2.0)

    job = asyncio.run(scenario())
    assert job is not None and job.name == "poll"


def test_idempotency_key_prevents_duplicates() -> None:
    async def scenario() -> tuple[str, str, int]:
        queue = MemoryJobQueue()
        first = await queue.enqueue(Job(name="a", payload={}, idempotency_key="poll:x"))
        second = await queue.enqueue(Job(name="a", payload={}, idempotency_key="poll:x"))
        return first, second, await queue.size()

    first, second, size = asyncio.run(scenario())
    assert first == second
    assert size == 1


def test_nack_requeues_with_backoff() -> None:
    async def scenario() -> tuple[int, float]:
        queue = MemoryJobQueue()
        job = Job(name="a", payload={}, max_attempts=3)
        await queue.enqueue(job)
        taken = await queue.dequeue(timeout=1)
        assert taken is not None
        await queue.nack(taken)
        return taken.attempts, taken.retry_delay()

    attempts, delay = asyncio.run(scenario())
    assert attempts == 1
    assert delay == pytest.approx(2.0)


def test_nack_discards_when_exhausted() -> None:
    async def scenario() -> int:
        queue = MemoryJobQueue()
        job = Job(name="a", payload={}, max_attempts=1, attempts=1)
        await queue.nack(job)
        return await queue.size()

    assert asyncio.run(scenario()) == 0


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
def test_registry_rejects_duplicate_name() -> None:
    registry: NamedRegistry[str] = NamedRegistry("demo")
    registry.add("x", "first")

    with pytest.raises(RegistryError, match="已存在同名注册"):
        registry.add("x", "second")

    registry.add("x", "second", override=True)
    assert registry.get("x") == "second"


def test_registry_reports_available_names() -> None:
    registry: NamedRegistry[int] = NamedRegistry("demo")
    registry.add("a", 1)

    with pytest.raises(RegistryError, match="未注册的 demo"):
        registry.get("b")


# --------------------------------------------------------------------------- #
# 事件总线
# --------------------------------------------------------------------------- #
def test_subscriber_failure_does_not_break_publisher() -> None:
    async def scenario() -> list[str]:
        bus = EventBus()
        received: list[str] = []

        @bus.subscribe("demo.done")
        async def _boom(event):
            raise RuntimeError("handler 崩了")

        @bus.subscribe("demo.done")
        async def _ok(event):
            received.append(str(event.payload["id"]))

        await bus.publish("demo.done", id="x1")
        return received

    assert asyncio.run(scenario()) == ["x1"]


def test_wildcard_subscriber_receives_all_events() -> None:
    async def scenario() -> int:
        bus = EventBus()
        count = 0

        @bus.subscribe("*")
        async def _all(event):
            nonlocal count
            count += 1

        await bus.publish("a.happened")
        await bus.publish("b.happened")
        return count

    assert asyncio.run(scenario()) == 2


# --------------------------------------------------------------------------- #
# provider 边界
# --------------------------------------------------------------------------- #
def test_progress_normalizes_string_state() -> None:
    """第三方 provider 直接传字符串时，必须在边界归一成枚举。

    否则下游 `is GenerationState.SUCCEEDED` 判断失效，任务会一直轮询到超时。
    """
    progress = GenerationProgress(
        external_id="echo-1", state="succeeded"  # type: ignore[arg-type]
    )
    assert progress.state is GenerationState.SUCCEEDED
    assert progress.state.terminal is True


def test_progress_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="done"):
        GenerationProgress(external_id="echo-1", state="done")  # type: ignore[arg-type]


def test_progress_clamps_percentage() -> None:
    over = GenerationProgress(external_id="x", state=GenerationState.RUNNING, progress=140)
    under = GenerationProgress(external_id="x", state=GenerationState.RUNNING, progress=-5)
    assert over.progress == 100
    assert under.progress == 0


# --------------------------------------------------------------------------- #
# 轮询链的健壮性
# --------------------------------------------------------------------------- #
def test_poll_keeps_chain_alive_when_store_raises() -> None:
    """轮询里 DB 抛错不能让链断掉。

    轮询 job 的 `max_attempts=1`：异常冒到 worker 会被直接丢弃，之后队列里
    再没有这条记录的 job，任务永久停在 running（前端一直转圈）。
    """

    class _BoomStore:
        async def load(self, record_id: str) -> None:
            raise RuntimeError("数据库抖动")

    class _BoomPipeline:
        kind = "test-poll-boom"
        capability = Capability.VIDEO

        def store(self, session: object) -> _BoomStore:
            return _BoomStore()

        async def build_request(self, record: object) -> None:  # pragma: no cover
            return None

    register_pipeline(_BoomPipeline)

    async def scenario() -> list[dict]:
        queue = MemoryJobQueue()
        runner = GenerationRunner(queue)
        job = Job(
            name=JOB_POLL,
            payload={"kind": "test-poll-boom", "record_id": "r1", "polls": 3},
            max_attempts=1,
        )
        await runner.handle_poll(job)  # 不应抛异常
        return queue.snapshot()

    jobs = asyncio.run(scenario())
    assert len(jobs) == 1
    assert jobs[0]["key"] == "poll:test-poll-boom:r1"


def test_retry_policy_skips_permanent_errors() -> None:
    """4xx 是永久错误：重试只会拖时间并覆盖真正的错误信息。"""
    submit_job = Job(name=JOB_SUBMIT, payload={}, max_attempts=3)
    assert GenerationRunner._retryable(BadRequest("未知模型"), submit_job) is False
    assert GenerationRunner._retryable(Forbidden("无权限"), submit_job) is False
    assert GenerationRunner._retryable(UpstreamError("供应商 502"), submit_job) is True

    last_try = Job(name=JOB_SUBMIT, payload={}, attempts=2, max_attempts=3)
    assert GenerationRunner._retryable(UpstreamError("供应商 502"), last_try) is False


# --------------------------------------------------------------------------- #
# 提交异常分类：只有「确定没送达」才允许自动重提（R3-02）
# --------------------------------------------------------------------------- #
from generation.spec import GenerationRecord  # noqa: E402
from infra.ai.base import GenerationRequest  # noqa: E402
from infra.ai.echo import EchoProvider  # noqa: E402


class _SubmitProbeStore:
    """只跟踪意图标记与终态，不碰数据库。"""

    def __init__(self) -> None:
        self.submit_attempts = 0
        self.external_id: str | None = None
        self.failed_with: str | None = None
        self.raised: BaseException | None = None
        self.submit_calls = 0

    async def load(self, record_id: str) -> GenerationRecord:
        return GenerationRecord(
            id=record_id,
            kind="",  # 由 _run_submit 按注册的 kind 覆写
            model="seedance-2.5",  # 解析到 echo 供应商
            prompt="p",
            status="queued",
            owner_id="u_001",
            submit_attempts=self.submit_attempts,
            external_id=self.external_id,
        )

    async def mark_submitting(self, record_id: str) -> None:
        self.submit_attempts += 1

    async def clear_submit_attempt(self, record_id: str) -> None:
        self.submit_attempts = 0

    async def mark_submitted(self, record_id: str, external_id: str) -> None:
        self.external_id = external_id

    async def mark_progress(self, record_id: str, progress: int) -> None: ...

    async def mark_succeeded(self, record_id: str, **kwargs: object) -> None: ...

    async def mark_failed(self, record_id: str, error: str) -> None:
        self.failed_with = error


def _run_submit(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    boom: BaseException | None = None,
    probe: _SubmitProbeStore | None = None,
) -> _SubmitProbeStore:
    """把 echo 的 submit 换成指定异常，跑一次 handle_submit，返回探针。"""
    probe = probe or _SubmitProbeStore()

    async def fake_submit(self: EchoProvider, request: GenerationRequest) -> str:
        probe.submit_calls += 1
        if boom is None:  # pragma: no cover - 传入 None 时用于「根本不该提交」的用例
            raise AssertionError("这个用例不该真的调用供应商")
        raise boom

    monkeypatch.setattr(EchoProvider, "submit", fake_submit)

    async def scenario() -> None:
        async def build_request(record: GenerationRecord) -> GenerationRequest:
            return GenerationRequest(
                capability=Capability.VIDEO,
                model="seedance-2.5",
                prompt="p",
                request_id=record.id,
            )

        class _ProbePipeline:
            pass

        pipeline = _ProbePipeline()
        pipeline.kind = kind  # type: ignore[attr-defined]
        pipeline.capability = Capability.VIDEO  # type: ignore[attr-defined]
        pipeline.store = lambda session: probe  # type: ignore[method-assign]
        pipeline.build_request = build_request  # type: ignore[method-assign]
        register_pipeline(pipeline)

        try:
            await GenerationRunner(MemoryJobQueue()).handle_submit(
                Job(name=JOB_SUBMIT, payload={"kind": kind, "record_id": "r1"})
            )
        except Exception as exc:  # noqa: BLE001
            # 可重试路径会把异常向上抛（交给 worker nack），探针记下来
            probe.raised = exc

    asyncio.run(scenario())
    return probe


def test_submit_timeout_keeps_marker_and_fails_for_manual_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读超时 = 请求已发出但结果未知：保留意图标记，置为「结果未知」，不自动重提。"""
    import httpx

    probe = _run_submit(monkeypatch, "test-submit-timeout", httpx.ReadTimeout("上游无响应"))

    assert probe.submit_attempts == 1, "清标记等于允许重提，重提赌错就是第二份账单"
    assert probe.raised is None, "结果未知的失败不该走 worker 重试"
    assert probe.failed_with is not None
    assert "提交结果未知" in probe.failed_with
    assert probe.external_id is None


def test_submit_connect_error_is_cleared_and_reraised_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接失败 = 必然没送达：清标记，异常上抛走 worker 的退避重试。"""
    import httpx

    probe = _run_submit(monkeypatch, "test-submit-connect", httpx.ConnectError("refused"))

    assert probe.submit_attempts == 0
    assert isinstance(probe.raised, httpx.ConnectError)
    assert probe.failed_with is None


def test_submit_4xx_is_cleared_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """明确拒绝（4xx）：结果没有歧义，清标记；4xx 不可重试，直接失败。"""
    probe = _run_submit(monkeypatch, "test-submit-4xx", BadRequest("未知模型"))

    assert probe.submit_attempts == 0
    assert probe.raised is None
    assert probe.failed_with is not None


def test_submit_5xx_is_treated_as_possibly_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """网关 502 不是「明确没送达」：上游很可能已经受理、只是回程失败（R4-01）。

    把它当「确定没送达」就会自动重提，重提一次 = 第二份账单。
    """
    from core.exceptions import UpstreamError

    probe = _run_submit(monkeypatch, "test-submit-502", UpstreamError("供应商网关 502"))

    assert probe.submit_calls == 1, "5xx 不得自动重提"
    assert probe.submit_attempts == 1, "意图标记要留着，这是「可能已计费」的唯一痕迹"
    assert probe.raised is None
    assert probe.failed_with is not None and "提交结果未知" in probe.failed_with


def test_submit_not_implemented_fails_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    """501 = 功能没实现，请求根本没发出去：文案不能说「可能已计费」（R4-03）。"""
    from core.exceptions import NotImplementedFeature

    probe = _run_submit(
        monkeypatch, "test-submit-501", NotImplementedFeature("配音生成待实现")
    )

    assert probe.submit_attempts == 0, "没发出去的请求不该留下意图标记"
    assert probe.raised is None, "501 不做无谓重试"
    assert probe.failed_with is not None
    assert "提交结果未知" not in probe.failed_with
    assert "提交生成失败" in probe.failed_with


def test_submit_prepare_failure_is_not_blamed_on_billing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mark_submitting` 抛错 = 还没调用供应商，不能说「为避免重复计费请核对」（R4-03）。

    脚手架生成的 store 占位实现就是这种情况：供应商提交次数为 0，
    但旧代码会显示「提交结果未知：请求已发出…」，把人引向核对账单。
    """
    from core.exceptions import NotImplementedFeature

    class _BrokenStore(_SubmitProbeStore):
        async def mark_submitting(self, record_id: str) -> None:
            self.submit_attempts += 1
            raise NotImplementedFeature("TODO: submit_attempts += 1")

    probe = _run_submit(monkeypatch, "test-submit-prepare", probe=_BrokenStore())

    assert probe.submit_calls == 0, "准备阶段就失败了，压根没调用供应商"
    assert probe.raised is None
    assert probe.failed_with is not None
    assert "提交准备失败" in probe.failed_with
    assert "提交结果未知" not in probe.failed_with


# --------------------------------------------------------------------------- #
# 同步供应商：submit 直接给终态，不需要轮询
# --------------------------------------------------------------------------- #
def test_sync_provider_settles_without_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """同步供应商（多数图片厂商）在 submit 里就出结果。

    框架必须认这种形态：拿到终态快照就直接落结果，**不再排一次轮询** ——
    否则每张图都白跑一跳，而且轮询会拿着一个不会变的 external_id 反复问。
    """
    from infra.ai.base import GenerationProgress, GenerationState

    settled: dict[str, object] = {}

    class _Store(_SubmitProbeStore):
        async def mark_succeeded(self, record_id: str, **kwargs: object) -> None:
            settled.update(kwargs)

        async def mark_failed(self, record_id: str, error: str) -> None:
            settled["error"] = error

    probe = _Store()

    async def fake_submit(self: EchoProvider, request: GenerationRequest) -> object:
        return GenerationProgress(
            external_id="task_sync_1",
            state=GenerationState.SUCCEEDED,
            progress=100,
            output_url="https://example.com/a.png",
            cover_url="https://example.com/a.png",
        )

    monkeypatch.setattr(EchoProvider, "submit", fake_submit)

    async def scenario() -> tuple[list[dict], str | None]:
        async def build_request(record: GenerationRecord) -> GenerationRequest:
            return GenerationRequest(
                capability=Capability.IMAGE, model="seedance-2.5", prompt="p"
            )

        class _Pipeline:
            kind = "test-sync-image"
            capability = Capability.IMAGE

            def store(self, session: object) -> _Store:
                return probe

            async def build_request(self, record: GenerationRecord) -> GenerationRequest:
                return await build_request(record)

        register_pipeline(_Pipeline)
        queue = MemoryJobQueue()
        await GenerationRunner(queue).handle_submit(
            Job(name=JOB_SUBMIT, payload={"kind": "test-sync-image", "record_id": "r1"})
        )
        return queue.snapshot(), probe.external_id

    jobs, external_id = asyncio.run(scenario())

    assert jobs == [], "同步供应商已经出结果，不该再排轮询"
    assert external_id == "task_sync_1", "task_id 仍要落库，方便对账"
    assert settled.get("output_url") == "https://example.com/a.png"


def test_poll_times_out_by_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时按**任务创建时间**算，而不是按轮询次数。

    `generation_timeout_sec` 曾经没有任何代码读取：真实预算是
    `max_polls × interval`（默认 150×2 = 300 秒），配置里却写着 1800 秒，
    一个 5 分钟的视频会在第 5 分钟被判超时。而且轮询次数在重启后归零，
    反复重启永远到不了上限 —— 只有墙钟能兜住。
    """
    from datetime import UTC, datetime, timedelta

    from core.config import settings

    failed: dict[str, str] = {}

    class _OldStore:
        async def load(self, record_id: str) -> GenerationRecord:
            return GenerationRecord(
                id=record_id,
                kind="test-timeout",
                model="seedance-2.5",
                prompt="p",
                status="running",
                owner_id="u_001",
                external_id="ext-1",
                # 比超时预算还老一分钟
                created_at=datetime.now(tz=UTC)
                - timedelta(seconds=settings.generation_timeout_sec + 60),
            )

        async def mark_failed(self, record_id: str, error: str) -> None:
            failed["error"] = error

        async def mark_progress(self, record_id: str, progress: int) -> None: ...

        async def mark_succeeded(self, record_id: str, **kwargs: object) -> None: ...

    class _Pipeline:
        kind = "test-timeout"
        capability = Capability.VIDEO

        def store(self, session: object) -> _OldStore:
            return _OldStore()

        async def build_request(self, record: object) -> None:  # pragma: no cover
            return None

    register_pipeline(_Pipeline)

    async def scenario() -> list[dict]:
        queue = MemoryJobQueue()
        await GenerationRunner(queue).handle_poll(
            Job(
                name=JOB_POLL,
                payload={"kind": "test-timeout", "record_id": "r1", "polls": 1},
                max_attempts=1,
            )
        )
        return queue.snapshot()

    jobs = asyncio.run(scenario())

    assert failed.get("error") == "生成超时，请重试"
    assert jobs == [], "已判超时就不该继续排轮询"


def test_disabled_owner_stops_submit_before_spending(monkeypatch: pytest.MonkeyPatch) -> None:
    """发起人已停用 → 不调用供应商、不留提交意图，直接失败。

    这一条是「跨模块查询」维度的验收点：`generation` 层自己不能认识 identity，
    只能通过 `core.ports.accounts` 这个注入口问一句。注入口没接上时返回 `None`，
    按放行处理（老档案、已删除账号不能被误伤）。
    """
    import core.ports.accounts as ports

    class _Disabled:
        async def is_active(self, user_id: str) -> bool:
            return False

    # identity 在 import 期已经注册了真实实现；这里换掉它而不是再注册一个
    # （重复注册会按「账号体系只能有一个权威来源」直接报错），monkeypatch 负责还原
    monkeypatch.setattr(ports, "_checker", _Disabled())
    probe = _run_submit(monkeypatch, "test-owner-disabled")

    assert probe.submit_calls == 0, "停用账号不该再触发供应商调用（那是真金白银）"
    assert probe.submit_attempts == 0, "连提交意图都不该落：落了一切重试都会被当成「结果未知」"
    assert probe.failed_with is not None and "停用" in probe.failed_with
