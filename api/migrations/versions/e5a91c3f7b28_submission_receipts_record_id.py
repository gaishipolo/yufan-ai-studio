"""提交回执：加 record_id 列 + run_id 加宽到 64

Revision ID: e5a91c3f7b28
Revises: e4b7c19d3a52
Create Date: 2026-11-24 22:10:00.000000

## 为什么

台账要推广到**所有**驱动，而两类驱动的 `run_id` 语义不同：

- `bailian` 的 `run_id` 是**归档 run id** = **幂等键**。同一逻辑提交重放时必须
  复用既有 external_id，绝不能再发一次付费 POST → 一个 run 只允许一行。
- 其余驱动（`agnes` / `grsai` / 两个视频驱动）没有归档 run，能拿到的只有业务
  记录 id，而**同一份记录会被提交多次**：
  1. 4xx 之后框架自动重试 3 次（`clear_submit_attempt` 清零后重投）；
  2. 人工重提 `modules/image/service.py::retry` **复用同一 `job_id`**，清掉
     `external_id` 后重新提交。

  → 这类驱动必须**按轮次分行**，否则人工重提会被台账误挡（返回上一轮的
  external_id → 用户拿回旧图）。

所以：

- 加 `record_id`（业务记录 id，可空、索引）：让「按业务记录查台账」成为可能
  —— 恢复器只认 `record.external_id`，它得能反查回执才知道这条记录提交过什么；
- `run_id` 加宽到 64：轮次键的形状是 `{record_id}#{轮次}`（record_id 是 32 位
  hex + 后缀已超 32）。保持可读，而不是塞哈希 —— 运维要能直接看懂一行回执
  属于哪条记录的哪一轮。

存量行的 `record_id` 留空：`bailian` 的回执以归档 run 为准，不需要反查业务
记录；留空即可回滚（不需要回填）。

## 可逆性

downgrade 把 `run_id` 收回 32 —— ⚠️ **轮次键（`{record_id}#{n}`，34 字符）会
放不下**，所以回滚前必须先清掉这类行（它们的 `record_id` 非空，据此筛）。
因此 downgrade 不做数据清理，只留注释提醒：**回滚前先按 `record_id` 非空筛出
并人工确认**。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5a91c3f7b28"
down_revision = "e4b7c19d3a52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "submission_receipts",
        sa.Column("record_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_submission_receipts_record_id",
        "submission_receipts",
        ["record_id"],
        unique=False,
    )
    # SQLite 不支持直接 ALTER COLUMN → 必须走 batch（重建表）
    with op.batch_alter_table("submission_receipts") as batch_op:
        batch_op.alter_column(
            "run_id",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    # ⚠️ 见模块 docstring：轮次键行（record_id 非空）在 run_id 收回 32 后放不下，
    # 回滚前必须先人工处理这些行。
    with op.batch_alter_table("submission_receipts") as batch_op:
        batch_op.alter_column(
            "run_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
    op.drop_index("ix_submission_receipts_record_id", table_name="submission_receipts")
    op.drop_column("submission_receipts", "record_id")
