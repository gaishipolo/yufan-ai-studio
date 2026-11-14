"""下线旧的「带筛选约束的封装模板」两张表

Revision ID: c6f0a2d7e813
Revises: a3c7e91b2d45
Create Date: 2026-11-14 09:55:00.000000

## 为什么删

用户拍板「按 `template_packager` 的能力完整迁移，原有的 `template_pack` 功能可以
覆盖」。C1 已经把配置层（`template_manifests` 四表）与 `/template-manifests`
接口做完；C2 又把真正的素材包 / 视频模板（`template_packs` 三表 +
`video_templates`）从 `modules/template` 整体搬进本模块。两套能力都在之后，
`template_pack_templates` / `template_pack_slots` 不再有任何读者：

- 服务、路由、权限点（`template_pack:*` 里只留 manifest 那三个）已同批删除；
- `/template-packs` 的六个端点下线，前端对应页面在收尾阶段一并移除。

留着一张没人读的表比删掉更糟：下一个人会以为它是活的，迁移时还会被
`compare_metadata` 逼着维护。

## 能不能回滚

**表结构能，数据不能**。`downgrade` 按原样重建两张表（含索引与外键），但已经
删掉的行不会回来。这是「能力被替代」而不是「误删」：真要不丢数据，先备份再升级。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6f0a2d7e813"
down_revision: Union[str, Sequence[str], None] = "a3c7e91b2d45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 先删槽位（它带指向主表的外键），再删主表。
    op.drop_table("template_pack_slots")
    op.drop_table("template_pack_templates")


def downgrade() -> None:
    op.create_table(
        "template_pack_templates",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("prompt", sa.String(length=20000), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_video_asset_id", sa.String(length=64), nullable=False),
        sa.Column("source_duration_sec", sa.Float(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("cover_url", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_template_pack_templates_created_at", "template_pack_templates", ["created_at"]
    )
    op.create_index("ix_template_pack_templates_status", "template_pack_templates", ["status"])
    op.create_index("ix_template_pack_templates_owner_id", "template_pack_templates", ["owner_id"])
    op.create_index(
        "uq_template_pack_templates_key", "template_pack_templates", ["template_key"], unique=True
    )

    op.create_table(
        "template_pack_slots",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("template_id", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("slot_kind", sa.String(length=16), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("filter_kind", sa.String(length=16), nullable=False),
        sa.Column("filter_tag_ids", sa.JSON(), nullable=False),
        sa.Column("filter_tag_match", sa.String(length=8), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["template_pack_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_template_pack_slots_created_at", "template_pack_slots", ["created_at"])
    op.create_index("ix_template_pack_slots_template_id", "template_pack_slots", ["template_id"])
    op.create_index(
        "uq_template_pack_slots_position",
        "template_pack_slots",
        ["template_id", "position"],
        unique=True,
    )
