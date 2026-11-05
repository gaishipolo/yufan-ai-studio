"""登录失败限流（进程内 LRU + 滑动窗口）。

登录是唯一「匿名可达 + 需要校验口令」的接口，没有限流时爆破成本只有一个 HTTP 往返。
`core.exceptions.RateLimited`（429）是它的对外表现。

三个刻意的取舍（前两条是第五轮评审逼出来的）：

1. **锁定不能挡住正确密码**。计数惩罚的是「猜错」这个动作，不是「这台机器上的所有人」。
   如果第 6 次请求无论密码对错都 429，那么任何人用 5 个乱码就能把一个已知用户名
   锁 15 分钟——未认证即可拒绝服务，而且`正确密码被拒`这种错误用户根本看不懂。
2. **渐进延迟而不是纯硬拒**。超过阈值后每次尝试先付 `penalty()` 秒的等待成本
   （0.5s 起、封顶 5s）。延迟对爆破同样致命，但不会把记错几次密码的真实用户关在门外。
3. **计数表有上界、会回收**。裸 `dict` 可以被匿名请求线性撑大：换一个用户名就是一条
   新记录，永不删除。这里用 `OrderedDict` 当 LRU（写入时 `move_to_end`），
   写过期扫描 + `MAX_KEYS` 封顶，两个维度都有界。

键是 `用户名|客户端 IP`，不是只按 IP：多人共用出口 IP（公司 NAT、反代）时，
只按 IP 会让一个人拖垮所有人；只按用户名则攻击者换个名字就绕过。
IP 的取法见 `core/net.py`——只有对端是可信代理时才读 `X-Forwarded-For`。
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque

from core.exceptions import RateLimited

MAX_FAILURES = 5
WINDOW_SEC = 15 * 60.0
# 上限：一个进程里最多同时跟踪多少个「用户名|IP」。超出按 LRU 淘汰最旧的。
MAX_KEYS = 10_000
BASE_PENALTY_SEC = 0.5
MAX_PENALTY_SEC = 5.0

_failures: OrderedDict[str, deque[float]] = OrderedDict()


def _prune(bucket: deque[float], now: float) -> None:
    cutoff = now - WINDOW_SEC
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


def _sweep(now: float) -> None:
    """从 LRU 头部回收「窗口内已无失败」的键。

    `record_failure` 每次都会 `move_to_end`，所以队首就是最久没动静的键；
    一旦它的最后一次失败落在窗口外，后面的键只会更晚 → 可以立即停。
    """
    while _failures:
        bucket = next(iter(_failures.values()))
        _prune(bucket, now)
        if bucket:
            return
        _failures.popitem(last=False)


def _existing(key: str, now: float) -> deque[float] | None:
    """只读路径用的查询：**不创建条目**。

    否则「随便编个用户名登录」也能往表里塞一条空记录，第一条取舍就白做了。
    """
    bucket = _failures.get(key)
    if bucket is None:
        return None
    _prune(bucket, now)
    return bucket if bucket else None


def penalty(key: str) -> float:
    """超过阈值后建议的等待秒数（0 表示正常放行）。

    调用方应先 `await asyncio.sleep(penalty(key))` 再走验密：
    这样「等待」发生在 CPU 密集的 `scrypt` 之前，也发生在返回值之前，
    对爆破者的成本是真实的，对真实用户只是多等半秒。
    """
    bucket = _existing(key, time.monotonic())
    if bucket is None or len(bucket) < MAX_FAILURES:
        return 0.0
    extra = len(bucket) - MAX_FAILURES + 1
    return min(BASE_PENALTY_SEC * extra, MAX_PENALTY_SEC)


def is_locked(key: str) -> bool:
    """失败次数是否**已用尽**允许值（第 `MAX_FAILURES + 1` 次才回 429）。

    允许的那 5 次仍然回 401：用户需要知道是"密码错了"，而不是"被锁了"——
    一上来就 429 会让人以为系统坏了（R5-01 的另一半）。
    **只用于决定错误码**，不用于决定是否验密。
    """
    bucket = _existing(key, time.monotonic())
    return bucket is not None and len(bucket) > MAX_FAILURES


def reject(key: str) -> None:
    """失败次数已达阈值时抛出 429（由调用方在验密**失败之后**调用）。"""
    if is_locked(key):
        minutes = int(WINDOW_SEC // 60)
        raise RateLimited(f"密码错误次数过多，请 {minutes} 分钟后再试")


def record_failure(key: str) -> None:
    now = time.monotonic()
    _sweep(now)
    bucket = _failures.get(key)
    if bucket is None:
        bucket = _failures[key] = deque()
    _prune(bucket, now)
    bucket.append(now)
    _failures.move_to_end(key)
    while len(_failures) > MAX_KEYS:
        _failures.popitem(last=False)


def reset(key: str) -> None:
    _failures.pop(key, None)


def clear() -> None:
    """仅测试使用：每个用例都该从干净的计数器开始。"""
    _failures.clear()


__all__ = [
    "BASE_PENALTY_SEC",
    "MAX_FAILURES",
    "MAX_KEYS",
    "MAX_PENALTY_SEC",
    "WINDOW_SEC",
    "clear",
    "is_locked",
    "penalty",
    "record_failure",
    "reject",
    "reset",
]
