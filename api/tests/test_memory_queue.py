"""内存队列的语义契约 —— 与 db 驱动对齐的那几条。

`QUEUE_DRIVER=memory` 是**默认配置**（`core/config.py`），所以「内存驱动差一点」
就等于「默认配置下少一层保护」。这组用例逐条钉住差异：

- 幂等键占到 ack/nack 为止（db 是 `WHERE status IN ('pending','running')` 的部分唯一索引）；
- 同键**续接**不新建条目（db 写进 `next_job` 列，ack 时原地转 pending）；
- `nack` 先记失败再判耗尽（反过来会多跑一次）；
- 耗尽留死信 + 释放幂等键（db 留 `status='dead'` 行并清 key）；
- `close()` 不清空数据（协议表最后一行）。
"""

from __future__ import annotations

import asyncio

from infra.queue.base import Job, job_execution
from infra.queue.memory import MemoryJobQueue


def _job(*, key: str | None = None, name: str = "image.poll", max_attempts: int = 3) -> Job:
    return Job(name=name, payload={}, idempotency_key=key, max_attempts=max_attempts)


def test_dequeue_keeps_the_key_until_ack() -> None:
    """领走 ≠ 结束：在途的 job 仍然占着幂等键。

    在 dequeue 就释放的话，「一条正在跑、又有人用同一个键投一条」会变成两条并存
    —— 而这条保护是 `generation.runner` 用来防双计费的。
    """

    async def scenario() -> tuple[str, str, int]:
        queue = MemoryJobQueue()
        first = _job(key="poll:t1")
        await queue.enqueue(first)
        claimed = await queue.dequeue(timeout=1.0)
        assert claimed is not None
        # 在途期间同键再投：应当拿回同一个 id，堆里不多一条
        again = await queue.enqueue(_job(key="poll:t1"))
        return claimed.id, again, await queue.size()

    claimed_id, again_id, size = asyncio.run(scenario())
    assert again_id == claimed_id, "在途期间同键入队必须命中同一条"
    # `size()` 与 db 驱动同口径：只数在途（含正在跑的）。这里应当只有在途那一条
    assert size == 1, f"堆里不该多出第二条，实际在途 {size} 条"


def test_ack_releases_the_key() -> None:
    async def scenario() -> tuple[str, str]:
        queue = MemoryJobQueue()
        first = await queue.enqueue(_job(key="poll:t1"))
        claimed = await queue.dequeue(timeout=1.0)
        assert claimed is not None
        await queue.ack(claimed)
        second = await queue.enqueue(_job(key="poll:t1"))
        return first, second

    first, second = asyncio.run(scenario())
    assert second != first, "ack 之后同键应当能投一条新的"


def test_nack_counts_the_attempt_before_checking_exhaustion() -> None:
    """`max_attempts=3` 就只执行 3 次。

    旧的顺序是「先判 exhausted 再 +1」：第 3 次失败时 `attempts` 还是 2，
    判不出耗尽，于是又投一轮 —— 实际执行 4 次。
    """

    async def scenario() -> tuple[int, int, list[dict[str, object]]]:
        queue = MemoryJobQueue()
        job = _job(key="poll:t1", max_attempts=3)
        await queue.enqueue(job)
        executions = 0
        while True:
            # 重投走指数退避（2s、4s…），超时要留够，否则会把「还在等重投」
            # 误判成「已经跑完」
            claimed = await queue.dequeue(timeout=6.0)
            if claimed is None:
                break
            executions += 1
            await queue.nack(claimed)
        return executions, await queue.size(), queue.dead_letters()

    executions, size, dead = asyncio.run(scenario())
    assert executions == 3, f"应当执行 3 次，实际 {executions} 次"
    assert size == 0
    assert dead, "耗尽后必须留一条死信记录"
    assert dead[-1]["attempts"] == 3


def test_dead_letter_releases_the_key_so_it_can_be_retried() -> None:
    """死信要放掉幂等键，否则同一个 key 想重投会永远撞在那条不动的记录上。"""

    async def scenario() -> tuple[str, str]:
        queue = MemoryJobQueue()
        job = _job(key="poll:t1", max_attempts=1)
        await queue.enqueue(job)
        claimed = await queue.dequeue(timeout=1.0)
        assert claimed is not None
        await queue.nack(claimed)
        fresh = await queue.enqueue(_job(key="poll:t1"))
        return job.id, fresh

    dead_id, fresh_id = asyncio.run(scenario())
    assert fresh_id != dead_id, "死信之后同键应当能重新入队"


def test_continuation_on_the_same_key_does_not_create_a_second_entry() -> None:
    """handler 在自己执行期里用同一个键排下一跳：不新建条目，ack 时原地转 pending。

    这是生成轮询的形状（`generation.runner` 每跳都排下一跳）。db 驱动把它写进
    `next_job` 列；内存驱动必须给出同样的结果，否则轮询链在默认配置下断掉。
    """

    async def scenario() -> tuple[str, int, int]:
        queue = MemoryJobQueue()
        job = _job(key="poll:t1")
        await queue.enqueue(job)
        claimed = await queue.dequeue(timeout=1.0)
        assert claimed is not None

        with job_execution(claimed):
            staged = await queue.enqueue(_job(key="poll:t1"))

        size_while_running = await queue.size()
        await queue.ack(claimed)
        size_after_ack = await queue.size()
        return staged, size_while_running, size_after_ack

    staged, size_while_running, size_after_ack = asyncio.run(scenario())
    assert staged != "", "staged 应当有 id（父任务的）"
    assert size_while_running == 1, "续接期间只有在途那一条，不该多出第二条"
    assert size_after_ack == 1, "ack 之后下一跳应当已经就位（父任务那条转成了 pending）"


def test_nack_drops_the_staged_continuation() -> None:
    """这一跳失败，它的下一跳也作废（db 在 nack 里同样清 `next_job`）。"""

    async def scenario() -> int:
        queue = MemoryJobQueue()
        job = _job(key="poll:t1", max_attempts=1)
        await queue.enqueue(job)
        claimed = await queue.dequeue(timeout=1.0)
        assert claimed is not None
        with job_execution(claimed):
            await queue.enqueue(_job(key="poll:t1"))
        await queue.nack(claimed)
        return await queue.size()

    assert asyncio.run(scenario()) == 0, "失败后不该留下下一跳"


def test_close_does_not_clear_queue_data() -> None:
    """协议表写着「`close()` 不许清空队列数据」，db 驱动也什么都不做。"""

    async def scenario() -> int:
        queue = MemoryJobQueue()
        await queue.enqueue(_job(key="keep:1"))
        await queue.close()
        return await queue.size()

    assert asyncio.run(scenario()) == 1


def test_stats_reports_pending_running_and_dead() -> None:
    """与 db 驱动的 `stats()` 同名同义，供 `app doctor` 与健康检查读。"""

    async def scenario() -> dict[str, int]:
        queue = MemoryJobQueue()
        # 先耗尽一条（max_attempts=1 → 一次 nack 即死信），再留一条 pending
        await queue.enqueue(_job(key="dead:1", max_attempts=1))
        claimed = await queue.dequeue(timeout=1.0)
        assert claimed is not None
        await queue.nack(claimed)
        await queue.enqueue(_job(key="pending:1"))
        return await queue.stats()

    stats = asyncio.run(scenario())
    assert stats["running"] == 0
    assert stats["dead"] == 1
    assert stats["pending"] == 1
