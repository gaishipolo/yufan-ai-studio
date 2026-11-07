"""infra: 队列持久化与事件 outbox

Revision ID: b5d9e2a4f617
Revises: d3e8f1a5c720
Create Date: 2026-11-07 19:30:00.000000

两张表都是**框架自带**的，不归属任何业务模块：

`queued_jobs` —— `QUEUE_DRIVER=db` 的落地表。内存队列重启即丢，
表现为「用户点了生成、进程发版后任务静默消失、记录永远停在 queued」。
两个关键列：

- `lease_expires_at` 是可见性超时：领取只打标记，`ack` 才删行；租约到期别人能接手。
  没有它，「领的人崩了」就变成状态永久卡在 running，比丢任务更难查。
- `idempotency_key` 用**部分**唯一索引（只约束 pending/running）。死信要留档排查，
  但不能继续占着幂等键，否则同一 key 想重投会被一条永远不动的死信挡回去。
  部分索引在 sqlite 与 postgres 都支持，所以不用「删列再插」那种绕法。

`outbox_events` —— 事件与业务行同一事务落库，由周期任务补投。
语义是**至少一次**（崩溃可能造成重复投递），消费方必须幂等；
要做到「恰好一次」需要分布式事务，本骨架不承诺做不到的事。
刻意没有 `delivering` 中间态：那需要租约与回收器，只会多一处
「卡在 delivering 永远不动」的故障模式。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5d9e2a4f617"
down_revision: Union[str, Sequence[str], None] = "d3e8f1a5c720"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_STATUS = sa.text("status IN ('pending', 'running')")


def upgrade() -> None:
    op.create_table(
        "queued_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("timeout_sec", sa.Float(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_queued_jobs_created_at", "queued_jobs", ["created_at"])
    op.create_index("ix_queued_jobs_ready", "queued_jobs", ["status", "run_at"])
    op.create_index(
        "uq_queued_jobs_active_idempotency_key",
        "queued_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=_ACTIVE_STATUS,
        sqlite_where=_ACTIVE_STATUS,
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_events_name", "outbox_events", ["name"])
    op.create_index("ix_outbox_events_created_at", "outbox_events", ["created_at"])
    op.create_index("ix_outbox_events_ready", "outbox_events", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_events_ready", table_name="outbox_events")
    op.drop_index("ix_outbox_events_created_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_name", table_name="outbox_events")
    op.drop_table("outbox_events")

    op.drop_index("uq_queued_jobs_active_idempotency_key", table_name="queued_jobs")
    op.drop_index("ix_queued_jobs_ready", table_name="queued_jobs")
    op.drop_index("ix_queued_jobs_created_at", table_name="queued_jobs")
    op.drop_table("queued_jobs")
