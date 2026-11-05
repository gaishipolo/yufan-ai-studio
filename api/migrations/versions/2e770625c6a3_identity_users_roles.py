"""identity: 用户、角色、角色权限、用户角色

四张表一起建：它们只服务于「账号体系」这一个能力，拆成多个迁移没有收益。

关联用 `role_id` 而不是 `role_code`（角色改名不用级联），
不建外键（与项目其他模块一致：删除由 service 显式检查，避免删角色被约束卡住）。

Revision ID: 2e770625c6a3
Revises: b7f3a1c2d4e5
Create Date: 2026-11-05 14:11:52.764767
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2e770625c6a3"
down_revision: str | Sequence[str] | None = "b7f3a1c2d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _identity_columns() -> list[sa.Column]:
    """四张表共用的主键与审计列。"""
    return [
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_users_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_users_is_active"), ["is_active"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_users_username"), ["username"], unique=True
        )

    op.create_table(
        "roles",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
    )
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_roles_code"), ["code"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_roles_created_at"), ["created_at"], unique=False
        )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        *_identity_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_permissions")),
        sa.UniqueConstraint(
            "role_id", "permission", name="uq_role_permissions_role_permission"
        ),
    )
    with op.batch_alter_table("role_permissions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_role_permissions_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_role_permissions_role_id"), ["role_id"], unique=False
        )

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("role_id", sa.String(length=32), nullable=False),
        *_identity_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_roles")),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )
    with op.batch_alter_table("user_roles", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_roles_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_roles_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_roles_role_id"), ["role_id"], unique=False
        )
        # 统计「角色下有多少人」走这条联合索引
        batch_op.create_index(
            "ix_user_roles_role_user", ["role_id", "user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("user_roles", schema=None) as batch_op:
        batch_op.drop_index("ix_user_roles_role_user")
        batch_op.drop_index(batch_op.f("ix_user_roles_role_id"))
        batch_op.drop_index(batch_op.f("ix_user_roles_user_id"))
        batch_op.drop_index(batch_op.f("ix_user_roles_created_at"))
    op.drop_table("user_roles")

    with op.batch_alter_table("role_permissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_role_permissions_role_id"))
        batch_op.drop_index(batch_op.f("ix_role_permissions_created_at"))
    op.drop_table("role_permissions")

    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_roles_created_at"))
        batch_op.drop_index(batch_op.f("ix_roles_code"))
    op.drop_table("roles")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_username"))
        batch_op.drop_index(batch_op.f("ix_users_is_active"))
        batch_op.drop_index(batch_op.f("ix_users_created_at"))
    op.drop_table("users")
