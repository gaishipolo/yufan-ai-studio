"""给创建任务 / 生图加请求幂等键。

Revision ID: a9d3c7e15b42
Revises: d6e3f7a19c42
Create Date: 2026-11-27 20:30:00.000000

客户端为一次提交意图生成 `idempotency_key`，服务端按
`(owner, key)` 唯一约束拦住重复请求；`idempotency_fingerprint` 用来区分
「同一次提交的重试」与「同键却换了参数」。两列都可空，老客户端不受影响。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3c7e15b42"
down_revision: Union[str, Sequence[str], None] = "d6e3f7a19c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "video_tasks",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "video_tasks",
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ux_video_tasks_author_idempotency",
        "video_tasks",
        ["author_id", "idempotency_key"],
        unique=True,
    )

    op.add_column(
        "image_jobs",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "image_jobs",
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ux_image_jobs_owner_idempotency",
        "image_jobs",
        ["owner_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_image_jobs_owner_idempotency", table_name="image_jobs")
    with op.batch_alter_table("image_jobs") as batch_op:
        batch_op.drop_column("idempotency_fingerprint")
        batch_op.drop_column("idempotency_key")

    op.drop_index("ux_video_tasks_author_idempotency", table_name="video_tasks")
    with op.batch_alter_table("video_tasks") as batch_op:
        batch_op.drop_column("idempotency_fingerprint")
        batch_op.drop_column("idempotency_key")