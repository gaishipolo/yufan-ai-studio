"""内存队列实现：开发 / 单测使用，进程重启即丢。

实现要点：

- 按 `run_at` 排序的堆，天然支持延迟投递；
- **幂等键占用到 ack/nack 为止**（与 db 驱动的部分唯一索引同一语义：
  `WHERE status IN ('pending','running')`）。在 dequeue 就释放的话，
  「一条正在跑、又有人用同一个键投一条」会变成两条并存 —— 而 `QUEUE_DRIVER`
  的默认值就是 memory，这条保护在默认配置下等于没有；
- `nack` 的顺序与 db 驱动对齐：**先 `with_attempt()` 再判 `exhausted`**。
  反过来会多跑一次（`max_attempts=3` 实际执行 4 次）；
- 耗尽后留一条可查的死信记录（db 驱动是 `status='dead'` 的行），
  否则「为什么这条没跑」在进程内没有任何痕迹；
- `close()` **不清空**队列数据（协议表最后一行写着「不许清空队列数据」，
  db 驱动的 `close()` 也什么都不做）。
"""

from __future__ import annotations

import asyncio
import heapq
from typing import Any

from core import trace
from core.logging import get_logger
from infra.queue.base import Job, current_job

logger = get_logger(__name__)

#: 死信记录上限：只是给排查留线索，不需要无限增长。
_DEAD_LIMIT = 200


class MemoryJobQueue:
    #: 进程内队列不会被别人接手：没有租约，worker 也就不续租（见 `infra/queue/base.py`）。
    lease_sec: float | None = None

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Job]] = []
        self._seq = 0
        #: 幂等键 → job id。**在途的也算占用**，与 db 驱动的部分唯一索引同口径。
        self._by_key: dict[str, str] = {}
        #: 已领走、尚未 ack/nack 的 job id。用于「不能删掉别人正在跑的那一行」。
        self._running: set[str] = set()
        #: 父 job id → (续接的下一跳, 它的**到期时刻**)。与 db 驱动的 `next_job` 列
        #: 同一语义：handler 用同一个幂等键排下一跳时（生成轮询的典型形状），
        #: 不新建条目，挂在父任务上，等 ack 时原地转成 pending。
        #:
        #: 记绝对到期时刻而不是 `delay`：`delay` 是从**入队那一刻**起算的，而 ack
        #: 发生在 handler 结束之后 —— 存 delay 会在 ack 时重新起算，把已经等过的
        #: 那段时间再加一遍。db 驱动存的是 `run_at` 的绝对时间，这里对齐它。
        self._next: dict[str, tuple[Job, float]] = {}
        #: 死信：与 db 驱动的 `status='dead'` 行对应，进程内只留最近若干条。
        self._dead: list[dict[str, Any]] = []
        self._cv = asyncio.Condition()
        self._closed = False

    # ------------------------------------------------------------------ 入队
    async def enqueue(self, job: Job, *, delay: float = 0) -> str:
        # 入队即快照 trace：业务不传也带着「谁触发的」，与 db 驱动保持一致
        if not job.trace_id:
            job.trace_id = trace.current()
        async with self._cv:
            key = job.idempotency_key
            parent = current_job()
            if parent and key and parent.idempotency_key == key:
                # 续接：handler 在**自己的执行期**里排下一跳（生成轮询的典型形状）。
                # 不新建条目、也不撞幂等键，挂到父任务上，等 ack 原地转 pending ——
                # 与 db 驱动把下一跳写进 `next_job` 列完全同构。
                #
                # **延迟必须一起带过去**：轮询节奏（`GENERATION_POLL_INTERVAL_SEC`
                # / 驱动的 `poll_interval_sec`）全靠这个 `delay` 落地。以前这里只存
                # job、ack 时按 `delay=0` 入堆，于是在默认配置（`QUEUE_DRIVER=memory`）
                # 下每一跳都被立刻重排，轮询退化成忙循环：实测单跳约 0.38 秒，
                # `GENERATION_MAX_POLLS=900` 在 ~5.7 分钟内就烧完 —— 一个正常要
                # 6-8 分钟的视频必然被判「生成超时，请重试」，而上游其实还在正常生成。
                loop = asyncio.get_running_loop()
                self._next[parent.id] = (job, loop.time() + max(0.0, delay))
                return parent.id
            if key and key in self._by_key:
                # 同键只留一条：pending 与 running 都算占用（与 db 驱动一致）
                return self._by_key[key]
            self._push(job, delay=delay)
            self._cv.notify()
        return job.id

    def _push(self, job: Job, *, delay: float) -> None:
        """放进堆并占住幂等键。**调用方必须持有 `_cv`。**"""
        loop = asyncio.get_running_loop()
        self._seq += 1
        heapq.heappush(self._heap, (loop.time() + max(0.0, delay), self._seq, job))
        if job.idempotency_key:
            self._by_key[job.idempotency_key] = job.id

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
                        # **不释放幂等键**：它一直占到 ack/nack 为止。释放早了，
                        # 同一个键在「这条还在跑」的时候就能再投一条进来。
                        self._running.add(job.id)
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
        """完成：这时才释放幂等键；有续接就把下一跳原地转成 pending。

        只释放**自己**占的那个：如果键已经指向另一条 job（理论上不该发生，
        但 db 驱动在这里也要靠 fencing 判断「这行还归不归我」），不能替别人清掉。
        """
        async with self._cv:
            self._running.discard(job.id)
            successor = self._next.pop(job.id, None)
            if successor is not None:
                # 原地转 pending：幂等键继续占着（这条链还没有结束），不新建条目。
                # 延迟按**到期时刻**折算：入队时就已经过了的点立刻可跑，与 db 驱动
                # ack 时读 `next_job.run_at` 同口径。
                staged, run_at = successor
                self._push(staged, delay=max(0.0, run_at - asyncio.get_running_loop().time()))
                self._cv.notify()
                return
            key = job.idempotency_key
            if key and self._by_key.get(key) == job.id:
                self._by_key.pop(key, None)

    async def release(self, job: Job) -> None:
        """被中断（停机 / 上层取消）：放回待执行，**不记一次失败**。

        不能复用 `nack`：它会 `with_attempt()`，而 `max_attempts=1` 的 job
        一次就 `exhausted` —— 停机这个「没有结论」的动作会被记成「执行失败」，
        生成轮询链直接进死信（2026-11-20 重启时实测到）。
        """
        job.lease_token = None
        async with self._cv:
            self._running.discard(job.id)
            # 这一跳的下一跳属于「执行成功之后」：中断了就作废（与 nack 同口径）
            self._next.pop(job.id, None)
            self._push(job, delay=0)
            self._cv.notify()

    async def nack(self, job: Job, *, requeue: bool = True) -> None:
        """失败：先记一次失败，再判是否还有额度 —— 与 db 驱动同一个顺序。

        反过来的话（先判后加）`max_attempts=3` 会实际执行 4 次：第 3 次失败时
        `attempts` 还是 2，判不出耗尽，于是又投了一轮。
        """
        job.lease_token = None
        job.with_attempt()
        dead = not requeue or job.exhausted

        async with self._cv:
            self._running.discard(job.id)
            # 续接的下一跳属于「这一跳成功之后」；这一跳失败了，它也作废
            # （db 驱动在 nack 里同样把 `next_job` 清空）
            self._next.pop(job.id, None)
            if dead:
                # 死信：**释放幂等键**（与 db 驱动 `idempotency_key = None` 同口径），
                # 否则同一个 key 想重投会永远撞在这条不再移动的记录上。
                key = job.idempotency_key
                if key and self._by_key.get(key) == job.id:
                    self._by_key.pop(key, None)
                self._record_dead(job, "重试次数用尽" if requeue else "调用方放弃重试")
                return
            # 幂等键继续占着（这条任务仍然是「在途」），直接回堆，不走 `enqueue`
            # 的幂等检查 —— 否则会命中自己那条记录、返回旧 id，任务静默丢失。
            self._push(job, delay=job.retry_delay())
            self._cv.notify()

    def _record_dead(self, job: Job, reason: str) -> None:
        entry = {
            "name": job.name,
            "id": job.id,
            "attempts": job.attempts,
            "reason": reason,
            "key": job.idempotency_key or "",
        }
        self._dead.append(entry)
        if len(self._dead) > _DEAD_LIMIT:
            del self._dead[0 : len(self._dead) - _DEAD_LIMIT]
        # 进程内队列没有可查的行，日志就是唯一线索：留 error 而不是 debug
        logger.error(
            "任务进入死信（重试次数用尽）：name=%s id=%s attempts=%d reason=%s",
            job.name,
            job.id,
            job.attempts,
            reason,
        )

    async def size(self) -> int:
        """在途数量：**含正在跑的**（与 db 驱动的 `pending + running` 同口径）。"""
        async with self._cv:
            return len(self._heap) + len(self._running)

    async def close(self) -> None:
        """关闭入口，但**不清空数据**。

        协议表写着「`close()` 不许清空队列数据」，db 驱动的 `close()` 也什么都不做。
        清空会让「关掉再查」变成 0 —— 那不是一个关闭动作该有的副作用。
        """
        async with self._cv:
            self._closed = True
            self._cv.notify_all()

    # ------------------------------------------------------------------ 运维
    async def stats(self) -> dict[str, int]:
        """各状态计数，供 `app doctor` 与排查用（与 db 驱动的 `stats()` 同名同义）。"""
        async with self._cv:
            return {
                "pending": len(self._heap),
                "running": len(self._running),
                "dead": len(self._dead),
            }

    # ------------------------------------------------------------------ 调试
    def snapshot(self) -> list[dict[str, Any]]:
        """仅测试使用：查看在途任务（含正在跑的，与 `size()` 同口径）。"""
        entries = [
            {"name": job.name, "key": job.idempotency_key, "attempts": job.attempts}
            for _, _, job in sorted(self._heap)
        ]
        return entries

    def dead_letters(self) -> list[dict[str, Any]]:
        """仅测试使用：最近的重试用尽记录。"""
        return list(self._dead)


Driver = MemoryJobQueue
