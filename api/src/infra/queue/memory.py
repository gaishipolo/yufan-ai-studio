"""内存队列实现：开发 / 单测使用，进程重启即丢。

实现要点：
- 按 `run_at` 排序的堆，天然支持延迟投递
- 幂等键索引，重复入队只保留一条
- nack 时指数退避重投
"""

from __future__ import annotations

import asyncio
import heapq
from typing import Any

from core import trace
from infra.queue.base import Job


class MemoryJobQueue:
    #: 进程内队列不会被别人接手：没有租约，worker 也就不续租（见 `infra/queue/base.py`）。
    lease_sec: float | None = None

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Job]] = []
        self._seq = 0
        self._by_key: dict[str, str] = {}
        self._cv = asyncio.Condition()
        self._closed = False

    # ------------------------------------------------------------------ 入队
    async def enqueue(self, job: Job, *, delay: float = 0) -> str:
        # 入队即快照 trace：业务不传也带着「谁触发的」，与 db 驱动保持一致
        if not job.trace_id:
            job.trace_id = trace.current()
        async with self._cv:
            key = job.idempotency_key
            if key and key in self._by_key:
                return self._by_key[key]

            loop = asyncio.get_running_loop()
            self._seq += 1
            heapq.heappush(self._heap, (loop.time() + max(0.0, delay), self._seq, job))
            if key:
                self._by_key[key] = job.id
            self._cv.notify()
        return job.id

    # ------------------------------------------------------------------ 出队
    async def dequeue(self, timeout: float = 1.0) -> Job | None:  # noqa: ASYNC109
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        async with self._cv:
            while not self._closed:
                now = loop.time()
                if self._heap:
                    run_at, _, job = self._heap[0]
                    if run_at <= now:
                        heapq.heappop(self._heap)
                        if job.idempotency_key:
                            self._by_key.pop(job.idempotency_key, None)
                        return job
                    wait_for = min(deadline - now, run_at - now)
                else:
                    wait_for = deadline - now

                if wait_for <= 0:
                    return None
                try:
                    await asyncio.wait_for(self._cv.wait(), timeout=wait_for)
                except TimeoutError:
                    # 只是这一段等待到期（可能是 job 的延迟到期，也可能是整体超时），
                    # 回到循环顶部重新判断，不能直接返回 None
                    if loop.time() >= deadline:
                        return None
                    continue
            return None

    # ------------------------------------------------------------------ 确认
    async def renew(self, job: Job) -> bool:
        """无事可做：进程内队列没有「被别人接手」这回事，所以永远不会丢租约。"""
        return True

    async def ack(self, job: Job) -> None:
        return None

    async def nack(self, job: Job, *, requeue: bool = True) -> None:
        if not requeue or job.exhausted:
            return
        job.lease_token = None
        job.with_attempt()
        await self.enqueue(job, delay=job.retry_delay())

    async def size(self) -> int:
        return len(self._heap)

    async def close(self) -> None:
        self._closed = True
        async with self._cv:
            self._heap.clear()
            self._by_key.clear()
            self._cv.notify_all()

    # ------------------------------------------------------------------ 调试
    def snapshot(self) -> list[dict[str, Any]]:
        """仅测试使用：查看在途任务。"""
        return [
            {"name": job.name, "key": job.idempotency_key, "attempts": job.attempts}
            for _, _, job in sorted(self._heap)
        ]


Driver = MemoryJobQueue
