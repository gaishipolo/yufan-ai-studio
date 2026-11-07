"""异步链路的 trace 传递。

## 它补的是哪个洞

`RequestContextMiddleware` 把 `X-Request-Id` 放进 contextvar，同步调用链里的日志
都带着它。但一旦跨过**进程边界**（队列、outbox），contextvar 就断了：

```
POST /tasks (request_id=7f3a)  →  入队  →  ...  →  worker 消费（request_id=-）
```

用户报「上传失败了」，你手上只有一个 job id；worker 的失败日志也只有 job id。
两边都在，就是接不起来。本模块负责在**入队/投递时快照**、在**消费时恢复**，
让异步段的日志沿用触发它的那个 request id。

## 为什么复用 `request_id` 而不是另造一个 trace id

这套系统没有分布式追踪（没有 span 树、没有采样），多一个 id 只会让日志里出现
两个都能当「链路 id」看的字段，然后有人混用。一次用户动作 = 一个 request id =
一个 trace id，语义足够且不会歧义。

## 边界

- `""` 表示「非请求触发」（定时任务、补偿、启动恢复）。不要为了好看填 `-`：
  空串能直接被 `if not trace_id` 判断，而 `-` 会被当成合法的 id 传下去。
- 恢复出的上下文只在**这一次执行**内有效，执行结束必须还原，
  否则同一个 worker 的后续任务会继承上一条的 id（日志里会出现「幽灵请求」）。
"""

from __future__ import annotations

from core.context import get_request_id, set_request_id

# contextvar 的默认值，表示「没有请求上下文」
UNKNOWN = "-"


def current() -> str:
    """当前 trace id；不在请求上下文里时返回空串。"""
    value = get_request_id()
    return "" if value == UNKNOWN else value


def adopt(trace_id: str) -> str:
    """进入异步执行上下文，返回「进入前」的值，供 `release()` 还原。"""
    previous = get_request_id()
    set_request_id(trace_id or UNKNOWN)
    return previous


def release(previous: str) -> None:
    set_request_id(previous)


__all__ = ["UNKNOWN", "adopt", "current", "release"]
