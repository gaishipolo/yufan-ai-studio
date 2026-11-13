"""template_pack: 封装模板的两张表

Revision ID: b7e3d9f2a418
Revises: c7e2a94d5b16
Create Date: 2026-11-13 14:30:00.000000

## 表名为什么不是 `template_packs`

`modules/template` 已经占了 `template_packs` / `template_pack_references` /
`template_pack_versions`。本模块是**另一套东西**——带筛选约束的封装模板——
但同属「模板」语义，沿用 `template_packs` 会让人分不清两张表各是什么。

于是主表叫 `template_pack_templates`（模块名 + 实体名），槽位表叫
`template_pack_slots`。读表名就知道谁是谁，不必翻 models.py。

## 为什么槽位独立成表

槽位要按 `position` 排序、按 `template_id` 查，还要有「同一模板内编号唯一」
的约束。塞进主表的 JSON 列，这三件事一件都做不到：JSON 列查不动，
「列出所有已发布模板的第 2 号槽位」会逼着扫全表再在 Python 里筛。

`filter_tag_ids` 确实是 JSON 数组（一级标签可以有多个），但它是**槽位内部的
一个字段**，不影响槽位本身要独立成行这件事。

## 能否回滚

能。两张表都是本模块新建的，`downgrade` 直接删掉即可；没有别的模块引用它们
（本模块与 `modules/template` 完全隔离）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e3d9f2a418"
down_revision: Union[str, Sequence[str], None] = "c7e2a94d5b16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "template_pack_templates",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("prompt", sa.String(length=20000), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_video_asset_id", sa.String(length=64), nullable=False),
        sa.Column("source_video_id_space", sa.String(length=8), nullable=False),
        sa.Column("source_duration_sec", sa.Float(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("cover_url", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_pack_templates")),
    )
    op.create_index(
        "uq_template_pack_templates_key",
        "template_pack_templates",
        ["template_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_template_pack_templates_status"),
        "template_pack_templates",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_template_pack_templates_owner_id"),
        "template_pack_templates",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_template_pack_templates_created_at"),
        "template_pack_templates",
        ["created_at"],
        unique=False,
    )
    # `updated_at` 不建索引：`TimestampMixin` 只给 `created_at` 标了 index=True，
    # 现有表（library_assets / video_templates / library_tags / replacement_runs）
    # 也一律没有 updated_at 单列索引。这里跟着项目惯例走，不自造。
    # 真正被查询用到的列（status / owner_id / template_id / position）才建索引。

    op.create_table(
        "template_pack_slots",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("template_id", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("slot_kind", sa.String(length=16), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id_space", sa.String(length=8), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("filter_kind", sa.String(length=16), nullable=False),
        sa.Column("filter_tag_ids", sa.JSON(), nullable=False),
        sa.Column("filter_tag_match", sa.String(length=8), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["template_pack_templates.id"],
            name=op.f("fk_template_pack_slots_template_id_template_pack_templates"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_pack_slots")),
    )
    op.create_index(
        op.f("ix_template_pack_slots_template_id"),
        "template_pack_slots",
        ["template_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_template_pack_slots_created_at"),
        "template_pack_slots",
        ["created_at"],
        unique=False,
    )
    # 同一模板内编号唯一：编号即 `{{Mixed N}}`，重号会让提示词指向谁变得不确定。
    op.create_index(
        "uq_template_pack_slots_position",
        "template_pack_slots",
        ["template_id", "position"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_template_pack_slots_position", table_name="template_pack_slots")
    op.drop_index(op.f("ix_template_pack_slots_created_at"), table_name="template_pack_slots")
    op.drop_index(op.f("ix_template_pack_slots_template_id"), table_name="template_pack_slots")
    op.drop_table("template_pack_slots")

    op.drop_index(op.f("ix_template_pack_templates_created_at"), table_name="template_pack_templates")
    op.drop_index(op.f("ix_template_pack_templates_owner_id"), table_name="template_pack_templates")
    op.drop_index(op.f("ix_template_pack_templates_status"), table_name="template_pack_templates")
    op.drop_index("uq_template_pack_templates_key", table_name="template_pack_templates")
    op.drop_table("template_pack_templates")
