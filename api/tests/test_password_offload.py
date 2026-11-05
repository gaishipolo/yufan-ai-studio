"""口令哈希不能阻塞事件循环（R6-02）。

`hashlib.scrypt(N=2**14)` 单次约 150ms，且会释放 GIL。问题是它一直是在
`async def authenticate` 里**同步**调用的：那 150ms 里整个事件循环被占住，
不是「登录变慢」，而是同一进程内的所有协程（健康检查、静态接口、别人的轮询）
一起停摆——10 个并发匿名登录就能把 API 按住 1.5 秒。

所以这里断言的不是「请求耗时」，而是**心跳协程的最大间隔**：
只要它超过 100ms，就说明事件循环被某段同步代码卡住了。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from core.security import _HASH_WORKERS, hash_password, verify_password_async
from modules.identity.errors import CredentialInvalid

# 心跳：每 5ms 醒一次，记录「实际等了多久 - 预期 5ms」的偏差。
HEARTBEAT_SEC = 0.005
MAX_LAG_SEC = 0.1


def _max_lag(ticks: list[float]) -> float:
    return max(ticks) if ticks else 0.0


def test_verify_password_does_not_block_the_event_loop() -> None:
    """并发验密期间，事件循环的最大停顿必须远小于一次 scrypt 的耗时。"""
    encoded = hash_password("pass-12345")  # 同步算一次，避免把测试本身算进停顿
    ticks: list[float] = []

    async def scenario() -> int:
        stop = False

        async def heartbeat() -> None:
            last = time.perf_counter()
            while not stop:
                await asyncio.sleep(HEARTBEAT_SEC)
                now = time.perf_counter()
                ticks.append(now - last - HEARTBEAT_SEC)
                last = now

        beat = asyncio.create_task(heartbeat())
        # 故意超过线程池容量：验证「排队」而不是「一拥而上」
        await asyncio.gather(
            *(verify_password_async("wrong-pass", encoded) for _ in range(_HASH_WORKERS * 3))
        )
        stop = True
        await beat
        return len(ticks)

    beats = asyncio.run(scenario())

    assert beats >= 10, "心跳一次都没跑起来，测试本身失效"
    assert _max_lag(ticks) < MAX_LAG_SEC, (
        f"事件循环被阻塞 {_max_lag(ticks) * 1000:.0f}ms：验密大概又回到同步调用了"
    )


def test_concurrent_logins_do_not_stall_the_event_loop(client) -> None:  # noqa: ANN001
    """接口层同一条不变式：N 个并发登录期间，事件循环仍然能调度别的协程。

    走真实 service：`authenticate` 里除了验密还有一次「用户不存在」的假哈希，
    两者都必须在线程池里算，所以这条用例比单测原语更接近线上。
    """
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    ticks: list[float] = []

    async def scenario() -> int:
        factory = get_session_factory()
        stop = False

        async def heartbeat() -> None:
            last = time.perf_counter()
            while not stop:
                await asyncio.sleep(HEARTBEAT_SEC)
                now = time.perf_counter()
                ticks.append(now - last - HEARTBEAT_SEC)
                last = now

        async def one(index: int) -> None:
            async with factory() as session:
                with pytest.raises(CredentialInvalid):
                    # 用户名各不相同 → 限流键各不相同 → 没有等待延迟，只有 CPU 成本
                    await IdentityService(session).authenticate(
                        f"no-such-user-{index}", "wrong-pass", client_ip="198.51.100.7"
                    )

        beat = asyncio.create_task(heartbeat())
        await asyncio.gather(*(one(i) for i in range(8)))
        stop = True
        await beat
        return len(ticks)

    asyncio.run(scenario())

    assert _max_lag(ticks) < MAX_LAG_SEC, (
        f"并发登录把事件循环卡住 {_max_lag(ticks) * 1000:.0f}ms"
    )
