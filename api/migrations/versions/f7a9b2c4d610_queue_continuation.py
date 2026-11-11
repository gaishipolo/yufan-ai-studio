"""Persist a fenced handler continuation without releasing its active key."""

from alembic import op
import sqlalchemy as sa

revision = "f7a9b2c4d610"
# 原本挂在 `e8f1a3c6b290` 上（PR 分支的父节点），而 main 从同一个点又走了
# `5d8c2c96676c → b7d1e4c92a36`。合并后必须挂到 main 的 head 上，否则两个 head，
# `alembic upgrade head` 直接报错。两处改动互不相干（队列列 / 替换状态列）。
down_revision = "b7d1e4c92a36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("queued_jobs", sa.Column("next_job", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("queued_jobs", "next_job")
