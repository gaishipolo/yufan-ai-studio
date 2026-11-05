"""进程内域事件总线。

用途：把「任务完成后要做什么」从主流程里摘出去。

    任务完成 ──publish──> 订阅者 A：发通知
                       ├─> 订阅者 B：写审计日志
                       └─> 订阅者 C：扣额度

这样同事要加「生成完成推送企业微信」，只需在自己的模块里写一个订阅函数，
**不改任务模块一行代码**，也不需要任务模块知道有他的存在。

可靠性边界（必须清楚）：
- 这是**进程内、尽力而为**的事件：handler 异常只记日志，不回滚主事务、不重试。
- 需要可靠投递（如计费）时，应改为「写 outbox 表 + 独立消费者」，本骨架预留了
  `EventBus.publish` 的 payload 序列化能力，替换实现即可，订阅方代码不变。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)

Handler = Callable[["DomainEvent"], Awaitable[None]]

WILDCARD = "*"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """一条域事件。`name` 建议用 `领域.动作` 形式，如 `task.completed`。"""

    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(slots=True)
class _Subscription:
    handler: Handler
    priority: int


class EventBus:
    """极简事件总线：同步注册、异步发布、失败隔离。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[_Subscription]] = {}

    def subscribe(self, name: str, *, priority: int = 100) -> Callable[[Handler], Handler]:
        """装饰器写法：`@bus.subscribe("task.completed")`。

        priority 小的先执行；同一 priority 按注册顺序。
        """

        def decorator(handler: Handler) -> Handler:
            self.add(name, handler, priority=priority)
            return handler

        return decorator

    def add(self, name: str, handler: Handler, *, priority: int = 100) -> None:
        subs = self._subs.setdefault(name, [])
        if any(s.handler is handler for s in subs):
            return
        subs.append(_Subscription(handler=handler, priority=priority))
        subs.sort(key=lambda s: s.priority)
        logger.debug("事件订阅 %s <- %s", name, getattr(handler, "__qualname__", handler))

    async def publish(self, name: str, **payload: Any) -> int:
        """发布事件，返回被调用的 handler 数量。

        永不向上抛异常：订阅者的失败不能影响发布方（例如通知失败不该让任务回滚）。
        """
        event = DomainEvent(name=name, payload=payload)
        handlers = [*self._subs.get(name, []), *self._subs.get(WILDCARD, [])]
        if not handlers:
            return 0

        results = await asyncio.gather(
            *(self._invoke(sub.handler, event) for sub in handlers),
            return_exceptions=True,
        )
        for handler_sub, result in zip(handlers, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "事件处理器失败 event=%s handler=%s",
                    name,
                    getattr(handler_sub.handler, "__qualname__", handler_sub.handler),
                    exc_info=result,
                )
        return len(handlers)

    @staticmethod
    async def _invoke(handler: Handler, event: DomainEvent) -> None:
        await handler(event)

    # ------------------------------------------------------------------ 视图
    def subscribers(self, name: str) -> int:
        return len(self._subs.get(name, [])) + len(self._subs.get(WILDCARD, []))

    def names(self) -> list[str]:
        return sorted(k for k, v in self._subs.items() if v)

    def clear(self) -> None:
        """仅测试使用。"""
        self._subs.clear()


@lru_cache
def get_event_bus() -> EventBus:
    return EventBus()


def reset_event_bus() -> None:
    """仅测试使用。"""
    get_event_bus.cache_clear()
