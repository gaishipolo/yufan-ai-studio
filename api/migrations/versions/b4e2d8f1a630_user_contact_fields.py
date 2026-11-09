"""identity: 用户表补手机号与职务两列

## 为什么改
用户与权限页按后台成员表格展示「手机号 / 职务」。这两个字段纯粹是通讯录信息，
不参与登录、找回密码或任何鉴权判断，所以不做唯一约束、不做格式校验。

## 旧数据怎么办
两列都 nullable，存量用户自动为 NULL，前端显示占位符即可；不回填任何默认值，
避免把「没填」和「填了空串」混成两种状态。

## 能否回滚
能。`downgrade()` 只删这两列，其它用户数据不动；已填的手机号/职务会丢失
（仅展示字段，无业务引用）。

Revision ID: b4e2d8f1a630
Revises: a3f1c7d9e520
Create Date: 2026-11-09 15:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e2d8f1a630"
down_revision: str | Sequence[str] | None = "a3f1c7d9e520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("phone", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("position", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("position")
        batch_op.drop_column("phone")
