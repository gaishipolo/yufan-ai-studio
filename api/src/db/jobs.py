"""任务队列的持久化表（`QUEUE_DRIVER=db` 使用）。

## 这张表解决的问题

`infra/queue/memory.py` 的队列在进程里，重启就没了。症状不是「报错」，而是
**悄悄少了一批活**：用户点了生图，队列里有 job、页面转圈，进程一重启（发版、
OOM、机器迁移），job 消失，记录永远停在 `queued`。生图模块后来专门写了
`image.reconcile` 周期任务去捞这种残留，那是在给内存队列打补丁。

这张表把「待执行的意图」变成和业务数据同一份持久化：只要事务提交了，
job 就一定还在，重启后接着跑。

## 为什么表在 `db/` 而不是 `infra/queue/`

项目约定是「框架自带的表都在 `db/`」（见 `db/registry.py`）：`load_all_models()`
无条件 import `FRAMEWORK_MODELS`，表才会进 `Base.metadata`，`create_all` 与
Alembic autogenerate 都能看见。表跟着驱动走（只有 `QUEUE_DRIVER=db` 才 import 它）
会让「用内存队列开发、上线切 db」时 autogenerate 报「检测到删除表」。
表在这里，驱动在 `infra/queue/db.py`，是本仓库唯一一条 `infra → db` 的依赖，
理由是那个驱动的职责**就是**持久化。

## 字段取舍

- `payload` 存 JSON **文本**，与 `audit_events.detail` 同理由：sqlite 要装扩展才有
  JSON 类型，postgres 是 jsonb，DDL 不一致；队列载荷不参与查询与聚合，存文本最省事。
- `lease_expires_at` 是**可见性超时**：领着跑的人崩了，租约到期后别人能接手。
  没有它，`status='running'` 就是永久卡死——比丢任务更难查。
- `lease_token` 是**领取凭据**（fencing token）：每次被领走换一个新值，续租/回执
  都必须带上它。只有 `lease_expires_at` 是不够的——一个「租约早已过期、自己还不知道」
  的 worker 会拿着旧 id 去 `ack`，把新主人正在跑的那一行删掉；那一行没了，
  新主人再崩一次，任务就凭空消失，连死信都没有。
- `idempotency_key` 用**部分唯一索引**（只约束 pending/running）：死信要留档排查，
  但它不能继续占着幂等键，否则「同一个 key 想重投」会被死信挡一辈子。
- `status` 只给 `(status, run_at)` 一个复合索引：取任务的查询就是「哪些能跑」。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, UUIDMixin, utcnow

# 待执行：`run_at` 到了就能被领走
JOB_PENDING = "pending"
# 已领取：在跑，靠 `lease_expires_at` 判断是不是崩了
JOB_RUNNING = "running"
# 死信：重试次数用尽（或明确不重试）。留档给人看，不再执行
JOB_DEAD = "dead"

# 「在途」= 占着幂等键的状态。查询幂等冲突时用它，别手写字符串
ACTIVE_JOB_STATUSES = (JOB_PENDING, JOB_RUNNING)


class QueuedJob(Base, UUIDMixin):
    __tablename__ = "queued_jobs"
    __table_args__ = (
        # 只对「在途」的 job 去重：死信不占键，历史记录可以留着
        Index(
            "uq_queued_jobs_active_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
        Index("ix_queued_jobs_ready", "status", "run_at"),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=JOB_PENDING, nullable=False)
    # 最早可执行时间（延迟投递就是把它往后推）
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 领取凭据：每次领走换一个。回执、续租都要对得上，防止「旧主人」写到新主人的行上。
    # 长度 32 是 uuid4().hex 的长度，比 id 短一截就够用（它不需要全局可读）。
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    timeout_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 入队那一刻的请求 id（= trace id）。异步之后日志会换一个执行上下文，
    # 没有它就只能靠 job id 反推「这是谁点的」。空字符串 = 非请求触发（定时/补偿）。
    trace_id: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    # One durable continuation, promoted only on successful fenced ack.
    next_job: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


__all__ = [
    "ACTIVE_JOB_STATUSES",
    "JOB_DEAD",
    "JOB_PENDING",
    "JOB_RUNNING",
    "QueuedJob",
]
