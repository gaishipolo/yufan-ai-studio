"""新增 `video_tasks.billing_mode`：按任务记录计费模式（免费 / 积分）

Revision ID: d5f8a2c7b914
Revises: c4e9a1b7d230
Create Date: 2026-11-16 09:40:00.000000

## 为什么改

LibTV 有两条上游队列：免费队列（`params.infiniteSwitch = 0`）与积分加速
队列（`infiniteSwitch = 1` + `disableBenefitTags`）。桥接层 `bridge.mjs` 靠
归档工作区 `run.json` 的 `billing_mode` 字段决定走哪一条，而那个字段此前由
`generic_inputs.py` **写死成 `free`** —— 也就是说积分加速那条代码路径在
通用链路上根本不可达，用户没有任何入口能选。

## 为什么按任务落库，而不是每次提交现读账号设置

这条值决定上游走哪条队列，而上游队列的选择与「是否真的扣费」绑定。一条
任务提交出去之后重试时，必须用**当初那一条**：账号权益可能变了（比如 0 积分
权益到期），若重试时现读设置就会换队列，等于用不同的计费口径重下单。

## 迁移什么

加一列 `billing_mode`，默认 `'free'`（保守的那一侧：没声明就排免费队列，
不会误扣积分）。存量行全部回填 `free` —— 它们提交时走的就只能是免费队列。

## 降级

直接删列即可，存量数据不丢（`free` 是默认值，删列后行为一致）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5f8a2c7b914"
down_revision: str | Sequence[str] | None = "c4e9a1b7d230"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        # 表里已经有行，NOT NULL 必须带 server_default 才加得上去。
        batch_op.add_column(
            sa.Column(
                "billing_mode",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'free'"),
            )
        )
    # server_default 留在库里会让「新增行漏写这个键」也悄悄成功。
    # 约束要回到应用层：模型 ORM 有 default，新建行照样能拿到 'free'。
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.alter_column("billing_mode", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.drop_column("billing_mode")
