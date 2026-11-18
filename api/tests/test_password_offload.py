"""口令哈希不能阻塞事件循环（R6-02）。

`hashlib.scrypt(N=2**14)` 单次约 150ms，且会释放 GIL。问题是它一直是在
`async def authenticate` 里**同步**调用的：那 150ms 里整个事件循环被占住，
不是「登录变慢」，而是同一进程内的所有协程（健康检查、静态接口、别人的轮询）
一起停摆——10 个并发匿名登录就能把 API 按住 1.5 秒。

所以这里断言的不是「请求耗时」，而是**心跳协程的累计停顿**：
偶发一拍慢是 CPU 调度抖动，持续累积停顿才是事件循环被同步代码占住了。

**工作量由观察窗口决定，不按轮数定额**：同一份代码在 CI runner 上单次 scrypt
比本机快一个量级，固定「8 个并发登录」只花 35ms，心跳只跳得动 7 拍，
`assert beats >= 10` 直接红——红的不是阻塞，是「窗口太短、根本没测到东西」
（run #63 就是这么红的）。按秒数跑，窗口在任何机器上都够长。

**单次尖峰或累计总量单独都不作数**：run #86 一拍 177ms 把 `max < 100ms` 顶红，
而全量测试机器负载高时每拍 9~43ms、累计 713ms 又会把「只看累计」顶红。
判据改成峰值与累计的双维度组合，见 `STALL_SPIKE_SEC` 等常量；判别力自检用
真实阻塞（把哈希换回同步调用）与两类抖动数据验过：抖动绿、真阻塞红。
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time

import pytest

from core import security
from core.security import _HASH_WORKERS, hash_password, verify_password_async
from modules.identity.errors import CredentialInvalid

# 心跳：每 5ms 醒一次，记录「实际等了多久 - 预期 5ms」的偏差。
HEARTBEAT_SEC = 0.005
# 观察窗口按墙钟时间收口，心跳只要证明「确实在跳」即可。
#
# 这个下限**不能定高**：它原本是 10，等于假设心跳每拍都准时。而在 2 核 CI
# runner 上，4 个 scrypt 线程把 CPU 吃满时，一跳从 5ms 变成 20ms 很正常——
# 那样窗口里只剩个位数拍，断言会在「窗口够长、确实没阻塞」的情况下变红。
# 拍数不是不变式，「窗口足够长」才是：`MIN_WINDOW_SEC` 负责后者，
# `test_verify_password_runs_off_the_event_loop_thread` 负责机制本身。
MIN_BEATS = 3
MIN_WINDOW_SEC = 0.2

# 判定「事件循环被卡住」要两个维度合起来看，只看最大值或只看累计都会误判：
# - run #86：CI 上偶发一拍 177ms，累计 179ms——单个尖峰是调度抖动；
# - 全量测试机器负载高时：每拍 9~43ms，累计 713ms——一堆中等尖峰也是抖动；
# - 真同步阻塞：8 并发 scrypt 串行，实测单拍 421ms / 累计 1212ms（重度），
#   或每次只占一个 scrypt 时单拍 150ms 但累计 1350ms（中度持续）。
# 所以判据是「单拍 >=300ms」或「单拍 >=120ms 且累计 >=600ms」。
# 结构性用例另外负责「确实 offload 了」这条不变式。
STALL_SPIKE_SEC = 0.3       # 重度：一次被占住几百 ms（多个 scrypt 串行）
STALL_SUSTAINED_SEC = 0.12  # 中度：至少一次 scrypt 的量级
STALL_TOTAL_SEC = 0.6       # 且持续累积远超观察窗口


@pytest.fixture
def full_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    """把哈希成本钉回生产值（`N=2**14`）。

    测试环境默认降到 `2**10`（见 `core.security._scrypt_n`，那是全量测试
    省下约 150s 的关键）。但**这两条用例的整个命题就是「一次 150ms 的
    scrypt 不该卡住事件循环」**：哈希快到 9ms 时心跳凑不够拍子，断言会从
    「没测出问题」滑成「测试本身失效」。需要真实成本的地方就显式要真实成本。
    """
    monkeypatch.setattr(security, "_scrypt_n", lambda: security._PROD_SCRYPT_N)


def _max_lag(ticks: list[float]) -> float:
    return max(ticks) if ticks else 0.0


def _stalled(ticks: list[float]) -> bool:
    """重度单拍，或中度单拍且持续累积，才算真阻塞；零星/中等抖动都放行。"""
    if not ticks:
        return False
    peak = max(ticks)
    if peak >= STALL_SPIKE_SEC:
        return True
    return peak >= STALL_SUSTAINED_SEC and sum(ticks) >= STALL_TOTAL_SEC


async def _drive(round_work) -> list[float]:  # noqa: ANN001
    """在 `MIN_WINDOW_SEC` 内反复跑 `round_work`，返回心跳记录的偏差序列。

    窗口按**墙钟时间**而不是轮数收口：机器快慢只影响做了多少轮，不影响
    「测了多久」——后者才是「最大间隔」这条断言有意义的前提。
    """
    ticks: list[float] = []
    stop = False

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not stop:
            await asyncio.sleep(HEARTBEAT_SEC)
            now = time.perf_counter()
            ticks.append(now - last - HEARTBEAT_SEC)
            last = now

    beat = asyncio.create_task(heartbeat())
    start = time.perf_counter()
    try:
        while time.perf_counter() - start < MIN_WINDOW_SEC:
            await round_work()
    finally:
        stop = True
        await beat
    return ticks


def test_verify_password_does_not_block_the_event_loop(full_cost: None) -> None:
    """并发验密期间，事件循环的最大停顿必须远小于一次 scrypt 的耗时。"""
    encoded = hash_password("pass-12345")  # 同步算一次，避免把测试本身算进停顿

    async def one_round() -> None:
        # 故意超过线程池容量：验证「排队」而不是「一拥而上」
        await asyncio.gather(
            *(verify_password_async("wrong-pass", encoded) for _ in range(_HASH_WORKERS * 3))
        )

    ticks = asyncio.run(_drive(one_round))

    assert len(ticks) >= MIN_BEATS, "心跳一次都没跑起来，测试本身失效"
    assert not _stalled(ticks), (
        f"事件循环被持续阻塞：峰值 {_max_lag(ticks)*1000:.0f}ms，"
        f"累计 {sum(ticks)*1000:.0f}ms；验密大概又回到同步调用了"
    )


def test_concurrent_logins_do_not_stall_the_event_loop(client, full_cost: None) -> None:  # noqa: ANN001
    """接口层同一条不变式：N 个并发登录期间，事件循环仍然能调度别的协程。

    走真实 service：`authenticate` 里除了验密还有一次「用户不存在」的假哈希，
    两者都必须在线程池里算，所以这条用例比单测原语更接近线上。
    """
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    factory = get_session_factory()
    rounds = itertools.count()

    async def one_round() -> None:
        # 每轮换用户名与来源 IP：不同轮次之间不该命中上轮留下的失败计数，
        # 否则迟到的是「被限流惩罚 5s」，而不是我们要测的 CPU 成本。
        base = next(rounds)
        client_ip = f"198.51.100.{base % 200 + 1}"

        async def one(index: int) -> None:
            async with factory() as session:
                with pytest.raises(CredentialInvalid):
                    await IdentityService(session).authenticate(
                        f"no-such-user-{base}-{index}", "wrong-pass", client_ip=client_ip
                    )

        await asyncio.gather(*(one(i) for i in range(8)))

    ticks = asyncio.run(_drive(one_round))

    assert len(ticks) >= MIN_BEATS, "心跳一次都没跑起来，测试本身失效"
    assert not _stalled(ticks), (
        f"并发登录把事件循环持续卡住：峰值 {_max_lag(ticks)*1000:.0f}ms，"
        f"累计 {sum(ticks)*1000:.0f}ms"
    )


def test_verify_password_runs_off_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """结构性断言：真正的哈希不在事件循环所在线程上算。

    上面两条靠时间说话，机器一快就什么都证明不了；这条不依赖速度——
    只要哪天有人把 `run_in_executor` 换成直接调用，线程名立刻对不上。
    """
    seen: list[str] = []
    real = security.verify_password

    def spy(raw: str, encoded: str) -> bool:
        seen.append(threading.current_thread().name)
        return real(raw, encoded)

    monkeypatch.setattr(security, "verify_password", spy)
    encoded = security.hash_password("pw")

    assert asyncio.run(verify_password_async("pw", encoded)) is True
    assert seen and seen[-1].startswith("scrypt"), (
        f"验密跑在 {seen} 上，而不是 scrypt 线程池里"
    )
