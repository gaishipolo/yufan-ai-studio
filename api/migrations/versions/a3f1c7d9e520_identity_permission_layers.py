"""identity: 个人-小组-团队三层权限授予

## 为什么改
用户模块从「扁平 RBAC（用户→角色→权限码）」升级为四层并集：
团队基础 ∪ 小组 ∪ 个人 ∪ 角色。团队 = 所有启用账号，所以不建成员表，
只在 `permission_grants` 里用 `scope='team'` + 固定 `scope_id` 存配置。

## 旧数据怎么办
三张表全是新表，**不改写任何存量绑定**：users / roles / role_permissions /
user_roles 一行都不动。升级后存量账号的有效权限只会增加（团队基础权限
在 bootstrap 时首次播种），不会缩水；这正是「个人权限永远 ≥ 小组/团队」
在存量数据上的兑现方式。

## 能否回滚
能。`downgrade()` 只删这三张新表，存量权限不受影响。代价是已经配置过的
小组与授权会丢失——它们是本次升级新增的数据，不是历史数据。

## 约定
与其它 identity 表一致：不建外键（删除由 repository 显式清绑定），
关联用 id 而不是名称，批次建索引用 `batch_alter_table` 兼容 SQLite。
表名用 `user_groups` 而非 `groups`：GROUPS 在 PostgreSQL 窗口帧语法里是关键字。

Revision ID: a3f1c7d9e520
Revises: e4b7c2a91f38
Create Date: 2026-11-09 14:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f1c7d9e520"
down_revision: str | Sequence[str] | None = "e4b7c2a91f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _identity_columns() -> list[sa.Column]:
    """与其它 identity 表共用的主键与审计列（对齐 UUIDMixin / TimestampMixin）。"""
    return [
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "user_groups",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        *_identity_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_groups")),
    )
    with op.batch_alter_table("user_groups", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_groups_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_groups_name"), ["name"], unique=True
        )

    op.create_table(
        "group_members",
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        *_identity_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_members")),
        sa.UniqueConstraint(
            "group_id", "user_id", name="uq_group_members_group_user"
        ),
    )
    with op.batch_alter_table("group_members", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_group_members_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_group_members_group_id"), ["group_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_group_members_user_id"), ["user_id"], unique=False
        )
        # resolve_identity 每请求按 user_id 查所属组，走这条联合索引
        batch_op.create_index(
            "ix_group_members_user_group", ["user_id", "group_id"], unique=False
        )

    op.create_table(
        "permission_grants",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        *_identity_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_grants")),
        sa.UniqueConstraint(
            "scope", "scope_id", "permission", name="uq_permission_grants_scope_code"
        ),
    )
    with op.batch_alter_table("permission_grants", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_permission_grants_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_permission_grants_scope", ["scope", "scope_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("permission_grants")
    op.drop_table("group_members")
    op.drop_table("user_groups")
