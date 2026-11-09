"""asset: 标签词表、素材关联与保存视图（M2-A）

Revision ID: c3f5a1d8e6b2
Revises: b7d2f0c4e815
Create Date: 2026-11-09 04:20:00.000000

三张表一起上，因为它们是一个整体：没有 `tags` 就没有 `asset_tags` 的意义，
没有 `asset_tags` 词表的 `use_count` 也无从算起，而保存视图存的就是
「按哪些标签筛」。分成三个迁移只会让中间那两个版本处于"能建词不能打标"的状态。

## 唯一约束为什么是全局 `name`

设计文档 §3.3 要求「同维度内唯一，且全局不重名」。全局唯一同时满足两条。
代价是"场景"维度里不能再有叫"特写"的词——但那本来就是两回事：
`shot.特写` 是景别，`scene.特写` 不是场景。

## `asset_tags` 为什么不是普通自增主键

`(asset_id, tag_id)` 复合主键本身就是「同一条素材不能重复打同一个词」的约束。
另加自增 id 会让重复关联变成一条可以存在的合法行，然后在"算交集"时
把结果算错（`COUNT(*) = N` 在重复行下会误判）。

## 不在这里插初始词表

46 个词由 `tag_service.ensure_vocabulary()` 幂等补齐（bootstrap + seed 都调）。
理由和分类树一致：迁移里 `INSERT` 之后，`app seed --reset` 按 metadata 清表
会把词表一起清掉，于是"演示库有词表、生产库没有"这种差异只在一种环境复现。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f5a1d8e6b2"
down_revision: Union[str, Sequence[str], None] = "b7d2f0c4e815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_into", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
    )
    op.create_index("uq_tags_name", "tags", ["name"], unique=True)
    op.create_index("ix_tags_dimension_status", "tags", ["dimension", "status"])

    op.create_table(
        "asset_tags",
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("tag_id", sa.String(length=32), nullable=False),
        sa.Column("added_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("asset_id", "tag_id", name=op.f("pk_asset_tags")),
    )
    op.create_index("ix_asset_tags_tag_asset", "asset_tags", ["tag_id", "asset_id"])

    op.create_table(
        "asset_views",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("query_json", sa.Text(), nullable=False),
        sa.Column("sort", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_views")),
    )
    op.create_index("uq_asset_views_owner_name", "asset_views", ["owner_id", "name"], unique=True)


def downgrade() -> None:
    # 反序删：`asset_tags` 与 `asset_views` 都引用 `tags.id` / 素材 id（逻辑引用，
    # 不是外键）。先删关联再删词表，否则会留下一批指向不存在标签的关联行，
    # 而那种状态没有可读的症状——筛选时只是"筛不出东西"。
    op.drop_index("uq_asset_views_owner_name", table_name="asset_views")
    op.drop_table("asset_views")
    op.drop_index("ix_asset_tags_tag_asset", table_name="asset_tags")
    op.drop_table("asset_tags")
    op.drop_index("ix_tags_dimension_status", table_name="tags")
    op.drop_index("uq_tags_name", table_name="tags")
    op.drop_table("tags")
