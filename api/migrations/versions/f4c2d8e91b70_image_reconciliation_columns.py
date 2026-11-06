"""image: 对账凭证与供应商形态快照

Revision ID: f4c2d8e91b70
Revises: e6b1c9a4d230
Create Date: 2026-11-06 03:00:00.000000

两列都是为了让「进程崩在提交成功后、结果落库前」这段窗口可恢复：

- `previous_external_id`：重试必须把 `external_id` 清掉才能重新提交，
  但那个 task_id 恰恰是「上一次可能已经扣费」的唯一对账凭证。清之前
  先存这里，事后才说得清这是第二次提交（R12-01）。
- `submit_mode`：启动恢复要判断"这条 running 孤儿能不能续轮询"，
  以前靠 `provider_for(record.model)` 按当前模型目录重新解析，供应商下线
  或换配置就会分错类。提交时把形态写进记录，历史就不再需要解释（R12-05）。

两列都允许 NULL：迁移前的老记录没有快照，恢复器按「不能续查」的安全
默认值处理；没有重试过的记录自然也没有 previous_external_id。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4c2d8e91b70"
down_revision: Union[str, Sequence[str], None] = "e6b1c9a4d230"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "image_jobs",
        sa.Column("previous_external_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "image_jobs",
        sa.Column("submit_mode", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_jobs", "submit_mode")
    op.drop_column("image_jobs", "previous_external_id")
