"""模板规格三张表（纯增量）

Revision ID: d4a9f1c73b20
Revises: c6f0a2d7e813
Create Date: 2026-11-14 13:05:00.000000

## 为什么是新的三张表，而不是给 manifest 加列

现有 `template_manifests` 四表把"一支原片 + 若干素材包 + 若干功能"写进了结构：
原片是必填单值、参考图只认图片、用途来自固定词表。要支持"纯提示词 / 多图 /
图+视频 / 加音频"，加列会变成加一列语义、再加一列例外。

新模型（`docs/TEMPLATE_SPEC.md`）：**槽位 + 两段提示词 + 模型绑定**。

| 表 | 作用 |
|---|---|
| `template_specs` | head：草稿 JSON + 生命周期 + 乐观锁 |
| `template_spec_versions` | 发布快照，只插入不更新 |
| `template_spec_slots` | 某个版本的槽位声明（`position` 即编号顺序） |

本版**只加表、不动老表**：替换链还在读 `video_templates`，一次性删会让本版变成
"半个迁移"。老表在 P3 统一时再删（见 `TEMPLATE_SPEC.md` §11.1）。

## 为什么草稿是一列 JSON

草稿只有本人写、整份替换，没有按行查询的需求；用行存反而要区分 `version=0`
与"发布中"两种半成品状态。发布时一次性展开成 version + slots，之后不可变。

## 索引为什么这么写

`tests/test_migrations.py::test_models_match_migrations` 用 `compare_metadata`
逐列逐索引比对：除显式声明的，还包含 `TimestampMixin.created_at` 的
`index=True` 自动索引（`ix_template_specs_created_at`）。`updated_at` 没有单列
索引，别顺手补。唯一约束必须建成 `UniqueConstraint`（不是唯一索引）——模型里
声明的是前者。

`template_spec_versions` / `template_spec_slots` 不带 `TimestampMixin`：它们是
不可变快照，`created_at`/`updated_at` 没有语义；版本表只有 `published_at`。

## 能否回滚

能。三张表都是本版新建、无外键，`downgrade` 直接 drop；不影响任何存量表。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a9f1c73b20"
down_revision: Union[str, Sequence[str], None] = "c6f0a2d7e813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "template_specs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("spec_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_specs")),
        sa.UniqueConstraint("spec_key", name="uq_template_specs_spec_key"),
    )
    op.create_index("ix_template_specs_owner_id", "template_specs", ["owner_id"], unique=False)
    op.create_index("ix_template_specs_status", "template_specs", ["status"], unique=False)
    op.create_index(
        op.f("ix_template_specs_created_at"), "template_specs", ["created_at"], unique=False
    )

    op.create_table(
        "template_spec_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("spec_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("prompt_locked", sa.Text(), nullable=False),
        sa.Column("prompt_editable", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("published_by", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_spec_versions")),
        sa.UniqueConstraint(
            "spec_id", "version", name="uq_template_spec_versions_spec_version"
        ),
    )
    op.create_index(
        "ix_template_spec_versions_spec", "template_spec_versions", ["spec_id"], unique=False
    )

    op.create_table(
        "template_spec_slots",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("slot_key", sa.String(length=40), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("binding", sa.String(length=16), nullable=False),
        sa.Column("ref_mode", sa.String(length=16), nullable=False),
        sa.Column("min_count", sa.Integer(), nullable=False),
        sa.Column("max_count", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("usage_text", sa.String(length=500), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=True),
        sa.Column("pack_id", sa.String(length=32), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_spec_slots")),
        sa.UniqueConstraint("version_id", "slot_key", name="uq_template_spec_slots_key"),
        sa.UniqueConstraint("version_id", "position", name="uq_template_spec_slots_position"),
    )


def downgrade() -> None:
    # 先删有唯一约束的两张，再删 head：顺序反了在 PG 上会因为引用顺序更啰嗦。
    op.drop_table("template_spec_slots")
    op.drop_table("template_spec_versions")
    op.drop_index(op.f("ix_template_specs_created_at"), table_name="template_specs")
    op.drop_index("ix_template_specs_status", table_name="template_specs")
    op.drop_index("ix_template_specs_owner_id", table_name="template_specs")
    op.drop_table("template_specs")
