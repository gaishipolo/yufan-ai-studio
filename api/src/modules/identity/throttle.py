"""登录失败限流（进程内滑动窗口）。

登录是唯一「匿名可达 + 需要校验口令」的接口，没有限流时爆破成本只有一个 HTTP 往返。
`core.exceptions.RateLimited`（429）早就定义好了，这里是它第一个真正的使用方。

实现放在模块内而不是 `core/`：限流策略（窗口多长、几次锁定）属于「登录」这个业务，
将来接 Redis 时也只换这一份实现，调用方（`IdentityService.authenticate`）不动。

两个刻意的取舍：
- **按 `用户名 + 客户端 IP` 计数**，不是只按 IP：多人共用出口 IP（公司 NAT、反代）
  时，只按 IP 会让一个人拖垮所有人；只按用户名则攻击者换个名字就绕过。
- **不区分「用户不存在」与「密码错误」**：计数键里带的是输入的用户名，
  不查库，所以不会因为「这个账号存不存在」而产生不同的锁定行为。
"""

from __future__ import annotations

import time
from collections import deque

from core.exceptions import RateLimited

MAX_FAILURES = 5
WINDOW_SEC = 15 * 60.0

_failures: dict[str, deque[float]] = {}


def _recent(key: str) -> deque[float]:
    bucket = _failures.setdefault(key, deque())
    cutoff = time.monotonic() - WINDOW_SEC
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    return bucket


def guard(key: str) -> None:
    """失败次数超限就拒绝，**在查库与校验口令之前**调用。

    先拦再算，被锁定期间连 `scrypt` 都不跑——否则限流只挡住了「登录」，
    没挡住「拿 CPU 换密码」这件事。
    """
    if len(_recent(key)) >= MAX_FAILURES:
        raise RateLimited(f"密码错误次数过多，请 {int(WINDOW_SEC // 60)} 分钟后再试")


def record_failure(key: str) -> None:
    _recent(key).append(time.monotonic())


def reset(key: str) -> None:
    _failures.pop(key, None)


def clear() -> None:
    """仅测试使用：每个用例都该从干净的计数器开始。"""
    _failures.clear()


__all__ = ["MAX_FAILURES", "WINDOW_SEC", "clear", "guard", "record_failure", "reset"]
