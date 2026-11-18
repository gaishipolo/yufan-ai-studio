"""`video_tasks` / `driver_jobs` 增加重试凭证列

Revision ID: d4a9c1e73b28
Revises: c2b7d4e91a06
Create Date: 2026-11-18 00:50:00.000000

## 为什么改

视频任务重试时无条件把 `external_id` 清成 NULL。而框架在「墙钟超时 / 轮询耗尽 /
供应商返回失败」三条路径上都保留任务号（`generation/runner.py` 只 `mark_failed`，
不清 `external_id`），所以「failed 且带任务号」是每一次已提交视频失败后的常态。

清号 = 第二次 `provider.submit()`（钱照花），而旧任务号**永久消失**：既没有列存它，
也没有审计。生图侧早在 R13-02 就把这条路堵上了（`previous_external_id` +
`external_id_history` + `confirmCharged`），视频这边一直缺。

## 迁移什么

两列，都可空，存量行不动：

- `previous_external_id`：最近一次被清掉的供应商任务号（页面/DB 可直接查）；
- `external_id_history`：JSON 数组，追加而非覆盖 —— 记录只有一个槽位，
  第二次确认会把第一次的冲掉。

## 降级

删两列即可。重试行为退回「直接清号」，与迁移前一致。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a9c1e73b28"
down_revision: str | None = "c2b7d4e91a06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("video_tasks") as batch:
        batch.add_column(sa.Column("previous_external_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("external_id_history", sa.Text(), nullable=True))
    # model_driver 只需要一个槽位：它的重试路径已经很窄（要 confirmCharged），
    # 没有 image 那种「第二次确认覆盖第一次」的多轮场景。
    with op.batch_alter_table("driver_jobs") as batch:
        batch.add_column(sa.Column("previous_external_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("driver_jobs") as batch:
        batch.drop_column("previous_external_id")
    with op.batch_alter_table("video_tasks") as batch:
        batch.drop_column("external_id_history")
        batch.drop_column("previous_external_id")
