"""audit: 通用操作留痕表

Revision ID: e6b1c9a4d230
Revises: c8d5e2f7a910
Create Date: 2026-11-06 02:10:00.000000

`audit_events` 是**操作留痕**，与 identity 的 `login_events`（安全审计）分开：
前者回答「谁提交了什么、参数是什么」，后者回答「谁试图登录、是否被限流」。
保留期、查询方式、写入时机都不同，合成一张表会让两边都别扭。

字段取舍：

- `actor_id` 允许为空——周期任务、恢复流程没有发起人；
- `detail` 是 JSON **文本**：只是当时参数的快照，不参与索引与聚合，
  存文本可以同时兼容 sqlite / postgres，不必为了 jsonb 给 sqlite 装扩展；
- 索引只给 `action` / `actor_id` / `target_id` / `created_at`：审计查询几乎都是
  「某动作」「某人」「某对象」「某时间段」四种入口之一。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b1c9a4d230"
down_revision: Union[str, Sequence[str], None] = "c8d5e2f7a910"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_target_id", "audit_events", ["target_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_target_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_table("audit_events")
