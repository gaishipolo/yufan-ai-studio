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

## `publish` 与 `publish_strict` 的区别（这条容易踩）

`publish()` 永不抛异常——订阅者失败只记日志。这对**请求路径**是对的（通知失败不该让
任务回滚），但对 **outbox 投递器**是致命的：投递器靠「抛不抛」判断要不要重试，
一个永不上抛的 `publish` 会让「订阅者失败」被记成「投递成功」，事件就此永久消失。

所以 outbox 用 `publish_strict()`：同样执行全部订阅者、同样记日志，但只要**任何**
一个失败就抛 `EventDeliveryError`。两个方法只差这一个布尔值，
不要为了「统一入口」把它合成一个带默认参数的函数——调用方看不出自己走的是哪条语义。
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


class EventDeliveryError(RuntimeError):
    """至少一个订阅者失败（只有 `publish_strict` 会抛）。

    `failures` 里带 handler 名字与原始异常：投递器把它整条写进 `last_error`，
    排查时不用再去日志里捞那一条 `事件处理器失败`。
    """

    def __init__(self, name: str, failures: list[tuple[str, BaseException]]) -> None:
        self.event = name
        self.failures = failures
        detail = "；".join(
            f"{handler}: {type(exc).__name__}: {exc}" for handler, exc in failures
        )
        super().__init__(f"事件 {name} 有 {len(failures)} 个订阅者失败：{detail}")


class EventBus:
    """极简事件总线：同步注册、异步发布、失败隔离。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[_Subscription]] = {}

    def subscribe(self, name: str, *, priority: int = 100) -> Callable[[Handler], Handler]:
        """装饰器写法：`@bus.subscribe("task.completed")`。

        执行语义是**组间串行、组内并发**：priority 小的那一组全部跑完，才开始
        下一组；同一 priority 内并发（相对顺序不保证，别写出依赖它的 handler）。

        为什么不是全串行：通知类 handler 会打外部 HTTP，串起来等于把延迟相加，
        一个渠道变慢就拖垮整条发布路径。默认 priority 都是 100，绝大多数情况下
        所有订阅者在同一组里并发，与「全并发」等价。
        为什么不是全并发：那就没法表达「先留痕、再通知」这种依赖，priority 只剩
        一个好看的数字。
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
        需要「失败能被看到」的调用方（outbox 投递器）用 `publish_strict`。
        """
        return await self._dispatch(name, payload, strict=False)

    async def publish_strict(self, name: str, **payload: Any) -> int:
        """发布事件，**任何一个订阅者失败就抛** `EventDeliveryError`。

        为什么必须有这一条：`db/outbox.py` 的投递器靠异常判断「这一条要不要重试」。
        如果它调的是永不抛的 `publish`，订阅者抛异常会被记成「投递成功」，
        事件表里写下 `delivered`，重试、退避、`failed` 留档全都不会发生——
        整条可靠投递的链路看起来齐全，实际一条都不会重试。
        """
        return await self._dispatch(name, payload, strict=True)

    async def _dispatch(
        self, name: str, payload: Mapping[str, Any], *, strict: bool
    ) -> int:
        event = DomainEvent(name=name, payload=payload)
        handlers = [*self._subs.get(name, []), *self._subs.get(WILDCARD, [])]
        if not handlers:
            return 0

        # 先排序再分组：`add` 只保证各自列表内有序，具名与通配是拼接起来的，
        # 不重排的话「通配 priority=10」会被排到具名 priority=100 后面。
        handlers.sort(key=lambda s: s.priority)

        failures: list[tuple[str, BaseException]] = []
        index = 0
        while index < len(handlers):
            level = handlers[index].priority
            group: list[_Subscription] = []
            while index < len(handlers) and handlers[index].priority == level:
                group.append(handlers[index])
                index += 1
            results = await asyncio.gather(
                *(self._invoke(sub.handler, event) for sub in group),
                return_exceptions=True,
            )
            for handler_sub, result in zip(group, results, strict=True):
                if not isinstance(result, BaseException):
                    continue
                label = getattr(
                    handler_sub.handler, "__qualname__", repr(handler_sub.handler)
                )
                failures.append((str(label), result))
                logger.error(
                    "事件处理器失败 event=%s handler=%s",
                    name,
                    label,
                    exc_info=result,
                )
        if failures and strict:
            raise EventDeliveryError(name, failures)
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
