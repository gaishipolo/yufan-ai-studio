"""启动恢复的门控与顺序（R13-01 / R13-03）。

R13 的形状是「门控贴在了错的那一层」：`worker_enabled` 表达的是「本实例不消费
队列、因此不做恢复」，却只贴给框架恢复器，模块自带的 `startup` 完全绕过去——
而恰好是自带 `startup` 的那两个模块（task / template）才真正需要门控。

所以这里不测函数返回值，而是把 `WORKER_ENABLED=false` 与真实 `ModuleSpec.startup`
放进同一个场景，断言「记录没有被改写、队列里没有无人消费的 job」。
组件级用例各自绿、组合起来破例，正是上一轮 346 个用例没能抓住它的原因。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from fastapi import APIRouter

import app as app_module
import generation.recovery as recovery
from api.module import ModuleSpec
from app import create_app, lifespan
from core.config import settings
from db.session import get_session_factory
from infra import get_queue
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask


def _spec(
    name: str,
    *,
    startup: Callable[[], Awaitable[None]] | None = None,
    kinds: tuple[str, ...] = (),
) -> ModuleSpec:
    return ModuleSpec(name=name, router=APIRouter(), startup=startup, recovery_kinds=kinds)


async def _enter_lifespan(app: object) -> None:
    async with lifespan(app) as _:  # type: ignore[arg-type]
        pass


async def _make_unknown_task() -> str:
    """造一条「提交结果未知」的视频任务：模块恢复器会把它判死。"""
    async with get_session_factory()() as session:
        task = VideoTask(
            title="提交结果未知的",
            prompt="极光",
            status=TaskStatus.RUNNING.value,
            model="Seedance 2.5",
            submit_attempts=1,
            author_id="u_001",
            author_name="老大",
        )
        session.add(task)
        await session.commit()
        return task.id


async def _load_task(task_id: str) -> VideoTask:
    async with get_session_factory()() as session:
        task = await session.get(VideoTask, task_id)
        assert task is not None
        return task


def test_worker_disabled_skips_both_recovery_implementations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """门控必须同时覆盖「模块自带 startup」和「框架恢复」两种实现。

    把门控写在 `recover_declared` 里（R12-09 的做法）只能覆盖后者，
    而当前三个模块里有两个走的是前者。
    """
    module_calls: list[str] = []
    framework_calls: list[str] = []

    async def module_startup() -> None:
        module_calls.append("module")

    async def fake_recover_declared(specs: object) -> dict[str, dict[str, int]]:
        framework_calls.append("framework")
        return {}

    monkeypatch.setattr(recovery, "recover_declared", fake_recover_declared)

    specs = [_spec("task", startup=module_startup, kinds=("video",))]
    result = asyncio.run(recovery.run_startup_recovery(specs, worker_enabled=False))

    assert result == {}
    assert module_calls == [], "worker 关闭时模块 startup 不许执行（R13-01）"
    assert framework_calls == []


def test_worker_enabled_runs_startup_then_framework_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向用例：门控不是「永远跳过」，顺序仍是「模块 startup 先、框架恢复后」。"""
    order: list[str] = []

    async def module_startup() -> None:
        order.append("module")

    async def fake_recover_declared(specs: object) -> dict[str, dict[str, int]]:
        order.append("framework")
        return {}

    monkeypatch.setattr(recovery, "recover_declared", fake_recover_declared)

    specs = [_spec("task", startup=module_startup, kinds=("video",))]
    asyncio.run(recovery.run_startup_recovery(specs, worker_enabled=True))

    assert order == ["module", "framework"]


def test_disabled_worker_lifespan_leaves_records_and_queue_untouched() -> None:
    """真实 lifespan：`WORKER_ENABLED=false` 时启动不许改写记录、不许塞队列。

    这条就是 R13-01 的探针 1 固化下来的回归锁：只读 API 实例每次启动都会把
    「提交结果未知」判死、发失败通知，并把 queued 记录投进无人消费的内存队列。
    """
    assert settings.worker_enabled is False, "本用例前提是 conftest 关掉了 worker"

    async def scenario() -> tuple[str, set[str], set[str]]:
        task_id = await _make_unknown_task()
        before = {job["key"] for job in get_queue().snapshot()}
        await _enter_lifespan(create_app())
        after = {job["key"] for job in get_queue().snapshot()}
        return task_id, before, after

    task_id, before, after = asyncio.run(scenario())

    assert [k for k in after - before if task_id in k] == [], (
        "worker 未启用还往队列里投任务：这些 job 没有任何消费者"
    )
    task = asyncio.run(_load_task(task_id))
    assert task.status == TaskStatus.RUNNING.value, "记录被启动恢复改写了"
    assert task.submit_attempts == 1
    assert task.error_message is None


def test_config_assertions_run_before_any_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产配置不合法时必须是零副作用失败，且不留 worker（R13-03）。

    以前两道断言在 lifespan 末尾：模块 startup 已经写库入队、worker 已经起来，
    而 `try: yield` 在那之后，`finally` 根本进不去——worker 后台任务与 engine
    都不会被回收。
    """
    called: list[str] = []

    async def module_startup() -> None:
        called.append("startup")

    spec = _spec("fake", startup=module_startup)
    monkeypatch.setattr(app_module, "active_modules", lambda: [spec])
    monkeypatch.setattr(settings, "app_env", "prod")

    def boom(app_env: str) -> None:
        raise RuntimeError("生产环境驱动配置缺失")

    # 断言是 lifespan 内部按需 import 的，patch 模块属性即可生效
    monkeypatch.setattr("infra.driver_config.ensure_prod_driver_settings", boom)

    app = create_app()
    with pytest.raises(RuntimeError, match="驱动配置缺失"):
        asyncio.run(_enter_lifespan(app))

    assert called == [], "配置校验之前就跑了模块 startup：副作用已经发生且无法回滚"
    assert not hasattr(app.state, "worker"), "启动失败时 worker 被创建了但不会被 stop"
