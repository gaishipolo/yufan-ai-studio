"""asset: 团队实体与成员关系（M1-A）

Revision ID: a1c4e8b72d90
Revises: e4b7c2a91f38
Create Date: 2026-11-09 03:40:00.000000

「团队」在这一版之前**不存在实体**：`task` 模块的 `team` 范围只是一个语义占位，
没有成员关系、没有管理界面，也没有任何数据能回答「这个团队有谁」。

为什么要先建它：分类树属于库，而库靠 `visibility + team_id` 表达。
`categories` 一旦要挂 `team_id`，团队就必须已经是一个真实存在的东西——
否则「团队库的分类」只能指向一个字符串常量，改名就散架。

## 为什么不在这里塞初始数据

三个团队与 6 个管理员账号由 seed 建（`modules/asset/seed.py` 与
`modules/identity/seed.py`）。理由和 `users` 表一样：迁移负责**结构**，
演示/初始数据由 `ModuleSpec.seed` 负责，`app seed --reset` 能重来。
把数据写进迁移，第二次部署就会主键冲突。

## 降级

按依赖反序删表。`team_members` 先删——它引用 `teams.id`（逻辑引用，不是外键），
先删主表会让成员关系指向不存在的团队，而那种状态没有可读的症状。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4e8b72d90"
down_revision: Union[str, Sequence[str], None] = "e4b7c2a91f38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("name", name=op.f("uq_teams_name")),
    )

    op.create_table(
        "team_members",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_members")),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )
    with op.batch_alter_table("team_members", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_team_members_team_id"), ["team_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_team_members_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("team_members", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_team_members_user_id"))
        batch_op.drop_index(batch_op.f("ix_team_members_team_id"))
    op.drop_table("team_members")
    op.drop_table("teams")
