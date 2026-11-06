"""image: 接入生成框架所需的状态列

Revision ID: c8d5e2f7a910
Revises: 4d2b8f6a1c90
Create Date: 2026-11-06 01:40:00.000000

生图原先只有「历史列表」，没有生成链路。接入 `generation` 框架后，
`image_jobs` 需要和 `video_tasks` 一样具备一组状态字段，框架才能把
「提交 / 轮询 / 落结果 / 失败」写回来：

- `status` / `progress` / `output_url` / `error_message`：进度与结果；
- `model` / `model_id`：可读名与模型目录 id（供应商路由按 id 走）；
- `submit_attempts`：调用供应商**之前**落的提交意图，>0 且无 `external_id`
  说明崩在「已受理 → 写库」之间，重启时不能自动重提（否则重复计费）；
- `started_at` / `finished_at`：耗时统计；
- `count`：一次请求的张数（Agnes 的 `n` 参数）。

对已有数据：`status` 的 server_default 是 `queued`，但演示库里那几条历史记录
带封面图且是「已完成」语义，迁移里显式改成 `succeeded`，避免历史页显示成
永远排队中。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d5e2f7a910"
down_revision: Union[str, Sequence[str], None] = "4d2b8f6a1c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("image_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("count", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="queued"
            )
        )
        batch_op.add_column(
            sa.Column("model", sa.String(length=64), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("model_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("output_url", sa.String(length=1024), nullable=True))
        batch_op.add_column(
            sa.Column("error_message", sa.String(length=512), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "submit_attempts", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(batch_op.f("ix_image_jobs_status"), ["status"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_image_jobs_model_id"), ["model_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_image_jobs_external_id"), ["external_id"], unique=False
        )

    # 演示数据里的历史记录本来就是「已完成」，别让它们显示成排队中
    op.execute(
        "UPDATE image_jobs SET status = 'succeeded', progress = 100 "
        "WHERE cover_url <> ''"
    )


def downgrade() -> None:
    with op.batch_alter_table("image_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_image_jobs_external_id"))
        batch_op.drop_index(batch_op.f("ix_image_jobs_model_id"))
        batch_op.drop_index(batch_op.f("ix_image_jobs_status"))
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("submit_attempts")
        batch_op.drop_column("error_message")
        batch_op.drop_column("output_url")
        batch_op.drop_column("progress")
        batch_op.drop_column("model_id")
        batch_op.drop_column("model")
        batch_op.drop_column("status")
        batch_op.drop_column("count")
