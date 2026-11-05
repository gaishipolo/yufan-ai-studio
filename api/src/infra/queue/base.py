"""任务队列抽象。

视频生成是长耗时任务，必须异步化。当前用内存队列跑通链路，
生产切 Redis/Celery/Arq 时只替换实现，业务层无感。

两个关键能力直接写进协议，因为它们决定架构形态：

1. **延迟投递**：`enqueue(job, delay=8)`。
   轮询型任务靠它做「N 秒后再查一次」，而不是在 handler 里 `sleep`。
   这样 worker 的并发槽位永远只被正在干活的 job 占用。
2. **幂等键**：同一个 key 重复入队只保留一条。
   生成任务的提交/轮询都是「至少一次」语义，幂等键避免重复扣费。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class Job:
    """一条待执行任务。"""

    name: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempts: int = 0
    max_attempts: int = 3
    # 幂等键：同键任务不会重复入队（如 "poll:task_123"）
    idempotency_key: str | None = None
    # 单次执行超时（秒），None 表示不限制
    timeout_sec: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def with_attempt(self) -> Job:
        self.attempts += 1
        return self

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts

    def retry_delay(self, *, base: float = 2.0, cap: float = 60.0) -> float:
        """指数退避：2s、4s、8s… 上限 60s。"""
        return min(cap, base * (2 ** max(0, self.attempts - 1)))


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue(self, job: Job, *, delay: float = 0) -> str: ...

    async def dequeue(self, timeout: float = 1.0) -> Job | None:  # noqa: ASYNC109
        ...

    async def ack(self, job: Job) -> None: ...

    async def nack(self, job: Job, *, requeue: bool = True) -> None: ...

    async def size(self) -> int: ...

    async def close(self) -> None: ...
