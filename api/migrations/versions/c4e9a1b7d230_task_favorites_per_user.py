"""收藏改成按账号存：新增 `task_favorites`，下线 `video_tasks.favorited`

Revision ID: c4e9a1b7d230
Revises: a7d3f0c85e21
Create Date: 2026-11-15 22:10:00.000000

## 为什么改

`video_tasks.favorited` 是**任务行上的全局布尔**，而出参文案写的是「当前账号
是否已收藏」。有 `task:read:all` 的账号互相看得见对方的任务，于是：A 收藏，
B 看到的是 true，B 点「取消收藏」就顺手把 A 的收藏清掉。共享可见下这不是
小概率事件，只要点开别人的卡片取消一次就会发生。

## 迁移什么

1. 建 `task_favorites(user_id, task_id)`，唯一约束 `(user_id, task_id)`；
2. 存量回填：把 `favorited = true` 的行按**作者**写一条收藏记录 —— 那个布尔本来
   就只可能代表「作者自己收藏过」（别人的开关和它共用一个值，分不出来），
   归给作者是最不坏的假设，也不会给别人凭空造出收藏；
3. 删列。

## 降级

能算回来，但**是有损的**：`favorited` 只有一个值，而降级后收藏属于「谁」无从
表达，所以把「存在至少一条收藏」写成 true —— 也就是说降级后原本别人收藏、
作者没收藏的任务会显示成作者也收藏了。真要回退到按行存的语义，本来就得接受
这个损失。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "c4e9a1b7d230"
down_revision: str | Sequence[str] | None = "a7d3f0c85e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FAVORITES = sa.table(
    "task_favorites",
    sa.column("id", sa.String),
    sa.column("user_id", sa.String),
    sa.column("task_id", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.create_table(
        "task_favorites",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_favorites")),
        sa.UniqueConstraint("user_id", "task_id", name="uq_task_favorites_user_task"),
    )
    op.create_index(
        op.f("ix_task_favorites_created_at"), "task_favorites", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_task_favorites_task_id"), "task_favorites", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_task_favorites_user_id"), "task_favorites", ["user_id"], unique=False
    )
    op.create_index(
        "ix_task_favorites_user_task", "task_favorites", ["user_id", "task_id"], unique=False
    )

    bind = op.get_bind()
    legacy = bind.execute(
        sa.text("SELECT id, author_id FROM video_tasks WHERE favorited")
    ).all()
    if legacy:
        now = datetime.now(tz=UTC)
        op.bulk_insert(
            _FAVORITES,
            [
                {
                    "id": uuid.uuid4().hex,
                    "user_id": author_id,
                    "task_id": task_id,
                    "created_at": now,
                    "updated_at": now,
                }
                for task_id, author_id in legacy
            ],
        )

    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.drop_column("favorited")


def downgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        # 先带 server_default 加列：表里已经有行，NOT NULL 无默认值会直接失败。
        batch_op.add_column(
            sa.Column("favorited", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.get_bind().execute(
        sa.text(
            "UPDATE video_tasks SET favorited = :yes WHERE id IN "
            "(SELECT task_id FROM task_favorites)"
        ).bindparams(yes=True)
    )

    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.alter_column("favorited", server_default=None)

    op.drop_index("ix_task_favorites_user_task", table_name="task_favorites")
    op.drop_index(op.f("ix_task_favorites_user_id"), table_name="task_favorites")
    op.drop_index(op.f("ix_task_favorites_task_id"), table_name="task_favorites")
    op.drop_index(op.f("ix_task_favorites_created_at"), table_name="task_favorites")
    op.drop_table("task_favorites")
