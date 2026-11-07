"""app_meta 加版本与审计

Revision ID: a1c4e7f2b930
Revises: dbbea50e7828
Create Date: 2026-11-07 22:58:00

## 为什么加这两列

`app_meta` 原来只有「最后写入者胜」。滚动发布时旧版本实例与新版本实例同时启动，
旧实例最后写就把快照退回旧集合，下一次启动把少掉的权限码当成「新增」又补进
内置角色——管理员手工摘掉的权限被悄悄加回来，全程无报错。

`version` 是 ORM 的乐观锁列（`version_id_col`）：任何 UPDATE 都自带
`WHERE version = <读到的值>`，被别人抢先改过就抛 `StaleDataError`。
`updated_by` 是最后一次写入者（`host:pid`），配合 `app_meta_history` 能回答
「谁把它改小的」。

`app_meta_history` 与主写入**同事务**：主写入回滚时审计行也消失，
否则会留下「改过但没改成」的记录，比没有更误导。每个键只保留最近若干条。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7f2b930"
down_revision: str | Sequence[str] | None = "dbbea50e7828"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 已有行必须拿到版本号，所以先带 server_default 加列。
    #
    # 这里分成两个 batch 块是**必须的**，不是风格问题：sqlite 的 batch 模式会
    # 建临时表 → `INSERT ... SELECT` 只带**原有**列 → 重命名。新列的值全靠它自己的
    # server_default 补。若在同一个 batch 里「加列 + 去默认值」，重建时那一列已经
    # 没有默认值了，而 SELECT 又不带它，于是 `NOT NULL constraint failed`——
    # 空库不会暴露这条（0 行），有数据的库一升就炸。`tests/test_migrations.py`
    # 的 `test_upgrade_keeps_existing_app_meta_rows` 钉的就是这个。
    with op.batch_alter_table("app_meta", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("updated_by", sa.String(length=64), nullable=False, server_default="")
        )

    # 再去掉默认值：留着会让 `compare_metadata` 与模型（Python 侧 default）不一致，
    # 也会让「以后忘了传版本」变成静默默认值而不是显式选择。
    with op.batch_alter_table("app_meta", schema=None) as batch_op:
        batch_op.alter_column("version", existing_type=sa.Integer(), server_default=None)
        batch_op.alter_column(
            "updated_by", existing_type=sa.String(length=64), server_default=None
        )

    op.create_table(
        "app_meta_history",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_meta_history")),
    )
    with op.batch_alter_table("app_meta_history", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_app_meta_history_key"), ["key"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("app_meta_history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_app_meta_history_key"))
    op.drop_table("app_meta_history")

    with op.batch_alter_table("app_meta", schema=None) as batch_op:
        batch_op.drop_column("updated_by")
        batch_op.drop_column("version")
