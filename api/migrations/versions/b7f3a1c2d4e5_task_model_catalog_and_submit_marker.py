"""task: model catalog id, style/reference params, submit attempts

Revision ID: b7f3a1c2d4e5
Revises: 844779d1b4c9
Create Date: 2026-11-05 12:30:00.000000

新增四列，各自解决一个具体问题：

- `model_id`：模型目录 id。`model` 保留可读名（展示与搜索），
  id 用于聚合、与 `GET /models` 对齐、以及供应商路由。
- `style_preset` / `reference_asset_ids`：契约里已经声明的字段，之前收了就丢。
- `submit_attempts`：调用供应商**之前**落的提交意图。>0 且 `external_id` 为空
  = 崩在「供应商已受理 → 写库」之间，重启时不能自动重提（否则重复计费）。

对已有数据：
- `model_id` 允许 NULL，读出时由 `ModelRouter` 按 label 反查补齐，不需要回填。
- `reference_asset_ids` 默认 `[]`，`submit_attempts` 默认 0。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f3a1c2d4e5"
down_revision: Union[str, Sequence[str], None] = "844779d1b4c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("model_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("style_preset", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "reference_asset_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column("submit_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_index(
            batch_op.f("ix_video_tasks_model_id"), ["model_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_video_tasks_model_id"))
        batch_op.drop_column("submit_attempts")
        batch_op.drop_column("reference_asset_ids")
        batch_op.drop_column("style_preset")
        batch_op.drop_column("model_id")
