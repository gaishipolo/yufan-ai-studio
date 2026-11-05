"""identity: 会话表与登录审计

Revision ID: 4d2b8f6a1c90
Revises: 9c1f4e2a7b83
Create Date: 2026-11-05 17:20:00.000000

两张表，各解决一个「令牌有状态」的需求：

- `user_sessions`：一次登录一行。access 令牌带 `sid`，解析器每请求校验一次
  「这个会话还在不在」，于是**登出、单设备踢下线、退出所有设备**才真的成立。
  刷新令牌不存原文，只存当前 `jti`——轮换时替换，旧的自然失效。
- `login_events`：一次登录尝试一行，只追加。失败原因单独一列
  （bad_password / disabled / rate_limited），因为「谁在什么时候从哪登录」
  是安全事件唯一的追溯依据，`users.last_login_at` 只存得下最后一次。

对已有数据：两张表都是新增，不影响现有行。已签发的 access 令牌没有 `sid`，
解析器按「无 sid 则跳过会话校验」处理——它们本来就只在 TTL 内有效，
不需要回填会话。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d2b8f6a1c90"
down_revision: Union[str, Sequence[str], None] = "9c1f4e2a7b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("refresh_jti", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=256), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
    )
    with op.batch_alter_table("user_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_sessions_user_id"), ["user_id"])
        batch_op.create_index(batch_op.f("ix_user_sessions_expires_at"), ["expires_at"])
        batch_op.create_index(batch_op.f("ix_user_sessions_created_at"), ["created_at"])

    op.create_table(
        "login_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=256), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_events")),
    )
    with op.batch_alter_table("login_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_login_events_user_id"), ["user_id"])
        batch_op.create_index(batch_op.f("ix_login_events_created_at"), ["created_at"])


def downgrade() -> None:
    with op.batch_alter_table("login_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_events_created_at"))
        batch_op.drop_index(batch_op.f("ix_login_events_user_id"))
    op.drop_table("login_events")

    with op.batch_alter_table("user_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_sessions_created_at"))
        batch_op.drop_index(batch_op.f("ix_user_sessions_expires_at"))
        batch_op.drop_index(batch_op.f("ix_user_sessions_user_id"))
    op.drop_table("user_sessions")
