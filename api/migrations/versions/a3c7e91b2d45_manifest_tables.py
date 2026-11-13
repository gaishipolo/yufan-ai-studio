"""manifest DB 化：配置层四张表（纯增量）

Revision ID: a3c7e91b2d45
Revises: f5b2d8c4a173
Create Date: 2026-11-14 00:50:00.000000

## 这一版只加表，不动别的

`template_packager` 原来把配置放在仓库里的 `manifest.json`，素材是同目录的
本地 mp4/png，发布靠一份 journal 进度文件 + 内容指纹保证幂等。改成数据库后：

- 配置 head / packs / references / functions 各一张表，两层结构与原 manifest 一一对应；
- 素材一律引用 `library_assets.id`（内容寻址），不再存路径、不再算文件字节摘要；
- 幂等由 `uq_template_manifests_key` + `fingerprint` 直接兜住，不需要进度文件。

**旧的两张 `template_pack_*` 表本版不动**：用户已拍板由 packager 能力覆盖，
但下线要连同 service / router / 权限点一起做（阶段 C），这里单独删表会让
本版变成「能回滚的加表」变成「半套迁移」。所以本版严格纯增量。

## 索引为什么写得这么啰嗦

`tests/test_migrations.py::test_models_match_migrations` 用 `compare_metadata`
逐列逐索引比对。除显式声明的，还包含 `TimestampMixin.created_at` 上那个
`index=True` 自动生成的 `ix_<table>_created_at` —— 漏一条就报「迁移缺索引」。
`updated_at` 没有单列索引，别顺手补。

## 能否回滚

能。四张表都是本版新建且无外键（全项目约定靠字符串 id + 应用层校验），
`downgrade` 直接 drop 即可，不影响任何存量表与数据。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c7e91b2d45"
down_revision: Union[str, Sequence[str], None] = "f5b2d8c4a173"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "template_manifests",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("video_asset_id", sa.String(length=64), nullable=False),
        sa.Column("video_shared", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_manifests")),
    )
    op.create_index(
        "uq_template_manifests_key", "template_manifests", ["template_key"], unique=True
    )
    op.create_index(
        "ix_template_manifests_owner_status",
        "template_manifests",
        ["owner_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_template_manifests_status", "template_manifests", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_template_manifests_created_at"),
        "template_manifests",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "template_manifest_packs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("manifest_id", sa.String(length=32), nullable=False),
        sa.Column("pack_key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reuse_pack_id", sa.String(length=32), nullable=False),
        sa.Column("required_roles", sa.JSON(), nullable=False),
        sa.Column("supported_scopes", sa.JSON(), nullable=False),
        sa.Column("wardrobe_mode", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("published_pack_id", sa.String(length=32), nullable=False),
        sa.Column("published_pack_version", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_manifest_packs")),
    )
    op.create_index(
        "uq_template_manifest_packs_key",
        "template_manifest_packs",
        ["manifest_id", "pack_key"],
        unique=True,
    )
    op.create_index(
        "uq_template_manifest_packs_position",
        "template_manifest_packs",
        ["manifest_id", "position"],
        unique=True,
    )
    op.create_index(
        op.f("ix_template_manifest_packs_created_at"),
        "template_manifest_packs",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "template_manifest_references",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("pack_id", sa.String(length=32), nullable=False),
        sa.Column("ref_key", sa.String(length=40), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_manifest_references")),
    )
    op.create_index(
        "uq_template_manifest_references_key",
        "template_manifest_references",
        ["pack_id", "ref_key"],
        unique=True,
    )
    op.create_index(
        "uq_template_manifest_references_position",
        "template_manifest_references",
        ["pack_id", "position"],
        unique=True,
    )
    op.create_index(
        "ix_template_manifest_references_asset",
        "template_manifest_references",
        ["asset_id", "pack_id"],
        unique=False,
    )

    op.create_table(
        "template_manifest_functions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("manifest_id", sa.String(length=32), nullable=False),
        sa.Column("function_key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("pack_key", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("scene_prompt_default", sa.Text(), nullable=True),
        sa.Column("input_mode", sa.String(length=24), nullable=False),
        sa.Column("media_order", sa.JSON(), nullable=False),
        sa.Column("bindings", sa.JSON(), nullable=True),
        sa.Column("scope_options", sa.JSON(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("published_template_id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_manifest_functions")),
    )
    op.create_index(
        "uq_template_manifest_functions_key",
        "template_manifest_functions",
        ["manifest_id", "function_key"],
        unique=True,
    )
    op.create_index(
        "uq_template_manifest_functions_position",
        "template_manifest_functions",
        ["manifest_id", "position"],
        unique=True,
    )
    op.create_index(
        op.f("ix_template_manifest_functions_created_at"),
        "template_manifest_functions",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("template_manifest_functions")
    op.drop_table("template_manifest_references")
    op.drop_table("template_manifest_packs")
    op.drop_table("template_manifests")
