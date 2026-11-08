"""trace 贯穿：入队快照 → 执行恢复 → 退出还原。

这三步缺任何一步都会让「异步之后日志接不回去」：
- 不快照：job 里根本没有 id；
- 不恢复：worker 的日志仍然只带 `-`；
- 不还原：同一个 worker 的下一条任务会继承上一条的 id，日志里出现幽灵请求。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from core import trace
from core.context import get_request_id, set_request_id
from infra.queue.base import Job
from infra.queue.memory import MemoryJobQueue
from workers import HANDLERS, Worker, register_handler


@pytest.fixture(autouse=True)
def _clean_context():
    """contextvar 在同一线程里会残留，每个用例前后都归零。"""
    set_request_id("-")
    yield
    set_request_id("-")


def test_adopt_and_release_roundtrip() -> None:
    assert trace.current() == ""
    previous = trace.adopt("req-1")
    assert trace.current() == "req-1"
    trace.release(previous)
    # 还原后必须回到「没有请求上下文」，否则日志会挂在一条不存在的请求上
    assert trace.current() == ""


def test_adopt_empty_means_no_context() -> None:
    previous = trace.adopt("")
    assert trace.current() == ""
    trace.release(previous)


async def test_memory_queue_stamps_trace_and_worker_restores_it() -> None:
    queue = MemoryJobQueue()
    seen: list[str] = []

    @register_handler("trace.demo")
    async def handler(job: Job) -> None:
        seen.append(get_request_id())

    set_request_id("req-abc123")
    try:
        await queue.enqueue(Job(name="trace.demo", payload={}))
        worker = Worker(queue, concurrency=1)
        await worker.start()
        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.01)
        await worker.stop()
    finally:
        HANDLERS.pop("trace.demo", None)

    assert seen == ["req-abc123"], "handler 里的日志拿不到触发它的请求 id"


async def test_db_queue_persists_trace_id(client) -> None:
    """db 驱动的 trace 必须落库：worker 可能在另一个进程里，内存态带不过去。"""
    from db.jobs import QueuedJob
    from db.session import get_session_factory
    from infra.queue.db import DbJobQueue

    queue = DbJobQueue()
    set_request_id("req-db-1")
    job = Job(name="trace.demo.db", payload={})
    try:
        await queue.enqueue(job)
    finally:
        set_request_id("-")

    async with get_session_factory()() as session:
        stored = await session.scalar(
            select(QueuedJob.trace_id).where(QueuedJob.id == job.id)
        )
        await session.execute(delete(QueuedJob).where(QueuedJob.id == job.id))
        await session.commit()
    assert stored == "req-db-1"


async def test_outbox_records_trace_and_restores_it_for_subscribers(client) -> None:
    """outbox 的投递在周期任务里发生：订阅方必须能看到写入时的 request id。"""
    from core.events import get_event_bus
    from db.outbox import OUTBOX_DELIVERED, OutboxEvent, dispatch_pending, record
    from db.session import get_session_factory

    seen: list[str] = []
    bus = get_event_bus()

    @bus.subscribe("trace.demo.event")
    async def _handler(event: object) -> None:
        seen.append(get_request_id())

    set_request_id("req-outbox-1")
    try:
        async with get_session_factory()() as session:
            event_id = await record(session, "trace.demo.event", value=1)
            await session.commit()
    finally:
        set_request_id("-")

    await dispatch_pending()
    assert seen == ["req-outbox-1"]

    # 投递完成后清场，避免污染后续用例的 outbox 计数
    async with get_session_factory()() as session:
        await session.execute(
            delete(OutboxEvent).where(
                OutboxEvent.id == event_id, OutboxEvent.status == OUTBOX_DELIVERED
            )
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# 入口：请求 id 必须真的进上下文
# --------------------------------------------------------------------------- #
# 这几条**直接驱动中间件**，不走 `client` + `caplog`。
#
# 为什么：初版是用 `caplog` 断言「日志里带上了这个 id」，单独跑绿、全量跑红——
# `caplog` 依赖 logger 的 `propagate`，而前面某个用例动过 logging 配置。
# 一条测试如果在「单独跑」和「全量跑」下结论不同，它守不住任何东西，
# 只会让 CI 变成随机数。直接读上下文里的值，与日志配置无关。
def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/probe",
        "raw_path": b"/probe",
        "query_string": b"",
        "headers": headers or [],
    }


async def _run_middleware(scope: dict) -> tuple[str, dict[bytes, bytes]]:
    """跑一遍中间件，返回「应用内部看到的 request id」和响应头。"""
    from api.middleware import RequestContextMiddleware

    seen: list[str] = []
    sent: list[dict] = []

    async def inner(scope, receive, send):
        seen.append(get_request_id())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    await RequestContextMiddleware(inner)(scope, receive, send)
    return seen[0], dict(sent[0].get("headers") or [])


async def test_middleware_puts_the_request_id_into_the_context() -> None:
    """请求 id 有两个出口：响应头和**上下文**（日志、DB 的 trace_id 都读它）。

    变异扫描把 `set_request_id(rid)` 删掉后，12 条观测性测试全绿——
    因为 `send_wrapper` 仍然把 `rid` 写进响应头，只看响应头的用例察觉不到。
    而日志里的 `request_id` 会全部退化成 `-`：一次请求的所有行失去关联，
    「按请求串联排查」这个能力静默消失，没有任何报错。
    """
    rid, headers = await _run_middleware(_scope([(b"x-request-id", b"rid-unit-9f2c1a")]))

    assert rid == "rid-unit-9f2c1a"
    assert headers[b"x-request-id"] == b"rid-unit-9f2c1a"


async def test_generated_request_id_is_used_by_both_outlets() -> None:
    """没带请求头时生成一个 id：响应头与上下文必须是**同一个**，不能各生成一次。"""
    import re

    rid, headers = await _run_middleware(_scope())

    assert re.fullmatch(r"[0-9a-f]{16}", rid)
    assert headers[b"x-request-id"] == rid.encode()


async def test_invalid_incoming_request_id_is_replaced() -> None:
    """外部传进来的 id 会被写进日志，必须过白名单：否则可以注入换行伪造日志行。"""
    rid, headers = await _run_middleware(
        _scope([(b"x-request-id", b"bad id\nwith newline")])
    )

    assert rid != "bad id\nwith newline"
    assert "\n" not in rid
    assert headers[b"x-request-id"] == rid.encode()


async def test_middleware_clears_the_previous_request_context() -> None:
    """先复位再写入：上一个请求的身份不能串到下一个请求里。

    `current_user` 只在认证成功时才写，所以「只覆盖 request id、不复位」会让
    **没带令牌的请求继承上一个请求的用户**——同一条连接、下一个请求，拿着别人的
    权限。这里先在上下文里放一个用户，再跑一遍中间件，断言内部看到的已经是空的。
    """
    from api.middleware import RequestContextMiddleware
    from core.context import CurrentUser, get_current_user, set_current_user

    set_request_id("req-previous")
    set_current_user(CurrentUser(id="u_stale", name="上一个请求", permissions=frozenset({"*"})))

    seen: list[tuple[str, str | None]] = []
    sent: list[dict] = []

    async def inner(scope, receive, send):
        user = get_current_user()
        seen.append((get_request_id(), user.id if user else None))
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    scope = _scope([(b"x-request-id", b"rid-fresh")])
    await RequestContextMiddleware(inner)(scope, receive, send)

    assert seen == [("rid-fresh", None)]

