"""任务队列抽象。

视频生成是长耗时任务，必须异步化。协议与实现分离，`QUEUE_DRIVER` 决定用哪个：

| 驱动 | 文件 | 适用 |
|---|---|---|
| `memory` | `infra/queue/memory.py` | 开发、单测、单进程演示（重启即丢） |
| `db` | `infra/queue/db.py` | 单机 / 小规模生产（job 落库，重启不丢，租约接管崩溃任务） |

两个关键能力直接写进协议，因为它们决定架构形态：

1. **延迟投递**：`enqueue(job, delay=8)`。
   轮询型任务靠它做「N 秒后再查一次」，而不是在 handler 里 `sleep`。
   这样 worker 的并发槽位永远只被正在干活的 job 占用。
2. **幂等键**：同一个 key 重复入队只保留一条。
   生成任务的提交/轮询都是「至少一次」语义，幂等键避免重复扣费。

## 协议只承诺这 7 个方法能表达的事

不是「随便什么队列都能接进来」，而是**这 7 个方法必须能映射到目标系统**。
写新的驱动前先对照下表，映射不上的实现会在某个角落撒谎：

| 协议方法 | Redis 对应 | 必须保证的语义 |
|---|---|---|
| `enqueue(job, delay)` | `ZADD`（score=到期时间） | 延迟到点之前不可见；同幂等键只留一条 |
| `dequeue(timeout)` | `ZRANGEBYSCORE` + `ZREM` | **原子领取**；领走后进程崩掉，别人要能接手 |
| `renew(job)` | 重新 `ZADD` 自己的条目 | **只延长自己的持有**（凭据对得上）；False = 已被接手 |
| `ack(job)` | `LREM` / `ZREM` pending | 幂等：重复 ack 不报错；**不能删掉别人正在跑的那一行** |
| `nack(job, requeue)` | 重新 `ZADD` 或进死信 | 重投要退避；次数用尽别无限重投；过期持有者不能抢回 |
| `size()` | `ZCARD` / `LLEN` | 只数在途（含正在跑的），不是历史总数 |
| `close()` | 关连接池 | 不许清空队列数据 |

## 为什么 `renew` 在协议里，而不是留给 worker 自己想办法

「领走就别管了」只在「handler 一定跑得比租约短」时成立，而那是**一条注释，不是一个机制**：
handler 偶尔慢一次（供应商接口卡 6 分钟、ffmpeg 排在大文件后面），任务就会被第二个
worker 重跑一遍——用户收到两份结果、供应商被扣两次费。

所以持有关系必须是**双向**的：worker 定期证明自己还活着（`renew`），队列在被别人
接手后明确告诉它「这行不归你了」（`renew` 返回 False）。worker 侧据此停手，
见 `workers.py` 的 `_renew_lease`。`lease_sec` 则由驱动给出，worker 按它的 1/4
决定续租节奏——内存驱动是 `None`（不会被别人接手，因此也不续租）。

## 两条纪律

1. **业务只拿协议**：`infra.get_queue()`，不要在模块里 `new` 具体类。
   换驱动时业务代码一行不改——这是「留入口」的全部意义。
2. **`attempts` 的语义是「已失败次数」，不是「已执行次数」**。
   handler 里读 `job.attempts + 1` 就是当前是第几次尝试。新驱动如果自己另算一套，
   日志与告警会全部错位。

## 未来接 Redis 的入口在哪

不在本文件，也不在业务模块：新增 `infra/queue/redis.py`（`Driver = RedisJobQueue`），
把 `QUEUE_DRIVER` 改成 `redis` 即可。需要一起提供的还有它自己的
`RedisQueueSettings`（见 `infra/driver_config.py`），连接参数不进全局 `Settings`。
`tests/test_config_guard.py` 会自动把它纳入配置巡检——不需要改测试。
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
    # 入队时的请求 id（trace）。驱动负责落库与读回，`workers.py` 负责在执行时
    # 恢复上下文；业务侧不需要关心，`enqueue()` 那一刻自动取（见 `core/trace.py`）。
    trace_id: str = ""
    # 领取凭据（fencing token）：由驱动在 `dequeue()` 时发放，续租与回执要带回来。
    # 业务侧不要读也不要改——它的全部意义是让驱动能判断「这行现在还归不归你」。
    lease_token: str | None = None
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
    #: 租约时长（秒）。`None` = 这个驱动不会「被别人接手」（进程内队列），无需续租。
    #: worker 按它的 1/4 决定 `renew()` 的节奏，所以它必须是**驱动真正在用的那个值**，
    #: 不能与内部实现各写一份（两处不一致时表现为「偶尔还是被执行两遍」）。
    lease_sec: float | None

    async def enqueue(self, job: Job, *, delay: float = 0) -> str: ...

    async def dequeue(self, timeout: float = 1.0) -> Job | None:  # noqa: ASYNC109
        ...

    async def renew(self, job: Job) -> bool:
        """延长自己手上的这条 job 的租约；返回 False 表示**已被别人接手**。

        成功续租返回 True。`lease_sec is None` 的驱动直接返回 True（无事可做）。
        """
        ...

    async def ack(self, job: Job) -> None: ...

    async def nack(self, job: Job, *, requeue: bool = True) -> None: ...

    async def size(self) -> int: ...

    async def close(self) -> None: ...
