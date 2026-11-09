"""replacement_runs: 记下「确认提交 → 出片」的起止时间

Revision ID: b8e4f1c7a902
Revises: e4b7c2a91f38
Create Date: 2026-11-09 16:55:00.000000

列表与调用档案要显示一条替换任务跑了多久。现有的两列都回答不了：

- `created_at` 是「准备输入」的时间，`prepared` 的档案可以在列表里放几天再点确认；
- `updated_at` 会被评价、回收站、补积分推着走，拿它当结束时间会把「评了一次」
  也算进耗时。

所以新增 `started_at`（status 进入 `queued` 时写）与 `finished_at`
（`success` / `failed` / `service_error` / `submission_unknown` 时写），
口径与写入点集中在 `modules/template/run_clock.py`。

**不回填历史行**：存量档案没有可信的提交时刻，两列留 `NULL`，页面显示「未记录」。
拿 `created_at` 或 `updated_at` 猜一个数，比不显示更糟——它会把排队时长、几天后
才确认的时间混进「生成耗时」里，而这个数字正是用来判断上游快慢的。

两列都可空，SQLite 直接 `ADD COLUMN` 即可，不需要 batch 重建表。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e4f1c7a902"
down_revision: Union[str, Sequence[str], None] = "e4b7c2a91f38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "replacement_runs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "replacement_runs",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("replacement_runs", "finished_at")
    op.drop_column("replacement_runs", "started_at")
