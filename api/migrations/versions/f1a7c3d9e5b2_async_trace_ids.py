"""异步链路 trace：queued_jobs / outbox_events 增加 trace_id

Revision ID: f1a7c3d9e5b2
Revises: b5d9e2a4f617
Create Date: 2026-11-07 20:30:00

为什么是两列而不是往 payload 里塞一个键：

- payload 是**业务契约**（handler 会 `**payload`），框架往里加字段迟早撞名；
- 列可以被索引、被 SQL 直接查（「这个 request id 落了几条任务」）；
- 空串默认值让「非请求触发」（定时任务、启动恢复）有一个明确的表示，
  不需要在业务代码里判断键是否存在。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a7c3d9e5b2"
down_revision: str | Sequence[str] | None = "b5d9e2a4f617"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch 模式：sqlite 不支持 ADD COLUMN ... DEFAULT 之后再改约束，
    # 统一走「建新表 → 拷数据 → 换名」，postgres 下等价于普通 ALTER。
    with op.batch_alter_table("queued_jobs") as batch:
        batch.add_column(
            sa.Column("trace_id", sa.String(length=64), server_default="", nullable=False)
        )
    with op.batch_alter_table("outbox_events") as batch:
        batch.add_column(
            sa.Column("trace_id", sa.String(length=64), server_default="", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_column("trace_id")
    with op.batch_alter_table("queued_jobs") as batch:
        batch.drop_column("trace_id")
