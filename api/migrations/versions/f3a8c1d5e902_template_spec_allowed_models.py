"""模板发布快照补一列 `allowed_models`：允许使用者挑选的模型清单

Revision ID: f3a8c1d5e902
Revises: c3d8e1f04a7b
Create Date: 2026-11-17 00:40:00.000000

## 为什么改

模板封装原本只有一个 `model` 字段（发布时冻结）。作者知道同一段提示词在好几款
模型上都成立，却没法把这层意思告诉使用者 —— 使用者只能拿到那个写死的模型。

于是加一列「允许选用的模型清单」：作者在草稿里声明，使用者从清单里挑。

## 为什么进版本表，而不是只留在草稿里

使用者读的是**发布快照**，不是草稿。清单是「这份模板能跑在哪几款模型上」的
一部分，它必须跟着版本走：草稿改了清单但没发版，使用者看到的仍是旧清单
（与 `model` 同一条理由 —— 它也是发布时冻结的）。

## 为什么空清单 = 老行为

存量模板一条都不改：默认 `[]`，即「只有固定模型」。不迁数据、不改行为，
老模板继续能用（这是「老模板必须继续能用」这条要求的落点）。

## 降级

直接删列即可：发布快照里少这一列 = 回到「只有固定模型」的老行为，存量数据不丢。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a8c1d5e902"
down_revision: str | Sequence[str] | None = "c3d8e1f04a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("template_spec_versions", schema=None) as batch_op:
        # 表里已经有行，NOT NULL 必须带 server_default 才加得上去。
        batch_op.add_column(
            sa.Column(
                "allowed_models",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
    # server_default 留在库里会让「新增行漏写这个键」也悄悄成功。
    # 约束要回到应用层：ORM 有 default=list，新建行照样能拿到空列表。
    with op.batch_alter_table("template_spec_versions", schema=None) as batch_op:
        batch_op.alter_column("allowed_models", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("template_spec_versions", schema=None) as batch_op:
        batch_op.drop_column("allowed_models")
