"""assets: 归档素材的共享 / 回收站 / 乐观锁

Revision ID: d3e8f1a5c720
Revises: c1d9e4b7a260
Create Date: 2026-11-06 22:20:00.000000

四列原先只存在于归档盘上的 `catalog/assets/*.json`（「JSON 即索引」时代）。
索引搬进 `assets` 表之后必须跟着搬，否则会出现两种错位：

- 列表按 DB 判定（没有 `deleted_at`），可见性按 JSON 判定 → 删掉的素材从另一条路径复活；
- 「公共原片」= 别人分享给我的，DB 里没有 `shared` 就查不出这个池子。

`state_version` 是乐观锁：改名 / 分享 / 移入回收站都是读-改-写，客户端拿旧版本
重试时要能分清「响应丢了」和「别人改过」。

`server_default` 只用于存量行回填。新写入一律走 ORM 默认值，不依赖数据库默认，
这样同一份代码在 sqlite 与 PG 上行为一致。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3e8f1a5c720"
down_revision: Union[str, Sequence[str], None] = "c1d9e4b7a260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "assets",
        sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
    )
    # 公共原片池的查询形状不带 owner：`category='source' AND shared AND deleted_at IS NULL`。
    # 部分索引只覆盖这一小撮行，写入开销可以忽略，却能把全表扫挡掉。
    op.create_index(
        "ix_assets_shared_source",
        "assets",
        ["category", "created_at"],
        unique=False,
        sqlite_where=sa.text("shared = 1 AND deleted_at IS NULL"),
        postgresql_where=sa.text("shared AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_assets_shared_source", table_name="assets")
    op.drop_column("assets", "state_version")
    op.drop_column("assets", "deleted_at")
    op.drop_column("assets", "shared_at")
    op.drop_column("assets", "shared")
