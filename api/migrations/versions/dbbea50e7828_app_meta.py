"""app_meta：框架级键值表

Revision ID: dbbea50e7828
Revises: f1a7c3d9e5b2
Create Date: 2026-11-07 21:37:50

第一个消费方是权限目录快照（`permissions.catalog`）：启动时要知道「这一版新增了
哪些权限点」，才能把它们补进内置角色，同时不把管理员手工移除的旧权限塞回去。
这份事实不能放内存（多副本各记各的）、不能放缓存（会过期）、不能放配置（运行期
写出），所以落一张两列表。

之后加键**不需要迁移**：这张表刻意没有列 schema。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dbbea50e7828"
down_revision: str | Sequence[str] | None = "f1a7c3d9e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_meta",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_meta")),
    )
    # `created_at` 的索引来自 `TimestampMixin`，全项目统一带；
    # 这张表很小，索引本身没什么用，但保留它才能与模型定义零漂移。
    with op.batch_alter_table("app_meta", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_app_meta_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("app_meta", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_app_meta_created_at"))
    op.drop_table("app_meta")
