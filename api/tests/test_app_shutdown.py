"""停机收尾：每一步都必须跑到，失败不能连累后面的步骤。

之前 `lifespan` 的 `finally` 是一串裸 `await`：调度器那一步抛出非
`TimeoutError` 的异常（任务内部抛 RuntimeError 之类）时，`finally` 直接中断，
后面四个收尾动作——`worker.stop()` / `close_http()` / `dispose_engine()` /
`shutdown_hash_pool()`——**一个都不会执行**。

后果：`--reload` 每轮漏一批数据库连接池与 keep-alive 连接，几分钟后开始
出现「连接被重置」与端口占用，而日志里什么线索都没有。
"""

from __future__ import annotations

import asyncio

from app import _shutdown_step


def test_shutdown_step_runs_the_action() -> None:
    called: list[str] = []

    async def _ok() -> None:
        called.append("ok")

    asyncio.run(_shutdown_step("正常步骤", _ok))
    assert called == ["ok"]


def test_shutdown_step_swallows_failure_and_keeps_going() -> None:
    """一步失败只记一笔账，不能让后面的步骤被跳过。"""
    called: list[str] = []

    async def _boom() -> None:
        called.append("boom")
        raise RuntimeError("连接池已经关了")

    async def _after() -> None:
        called.append("after")

    asyncio.run(_shutdown_step("会炸的步骤", _boom))
    asyncio.run(_shutdown_step("后面的步骤", _after))
    assert called == ["boom", "after"]


def test_shutdown_steps_run_in_order_even_when_one_fails() -> None:
    """三个收尾步骤按顺序跑完，失败的只是自己那一步。

    `lifespan` 本身要起整套应用，性价比不高，而 `_shutdown_step` 才是这段
    逻辑的实际承载——这里验的就是「彼此独立」这一条。
    """
    stopped: list[str] = []

    class _Worker:
        async def stop(self) -> None:
            stopped.append("worker")
            raise RuntimeError("队列已经停了")

    async def _close_http() -> None:
        stopped.append("http")

    async def _dispose_engine() -> None:
        stopped.append("engine")

    async def _scenario() -> None:
        await _shutdown_step("队列 worker", _Worker().stop)
        await _shutdown_step("出站连接池", _close_http)
        await _shutdown_step("数据库引擎", _dispose_engine)

    asyncio.run(_scenario())
    assert stopped == ["worker", "http", "engine"]
