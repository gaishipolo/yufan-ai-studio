"""template: 素材模板、模板版本与试跑记录（M3.5-A）

Revision ID: f4c1a9e7d2b3
Revises: e5b2c8d4f731
Create Date: 2026-11-09 09:10:00.000000

## 为什么是纯新增

模板是封装的继任者，但这一版**不迁移封装数据**：`pack_id → subject_id` 的映射
已经由 M2.5 建好，旧封装的读路径照旧。模板表从空开始，种子数据由
`template/seed.py` 造两个演示模板（一个可发布、一个缺角色）。

## 槽位存 JSON 而不是拆表

槽位是**整块**读写的声明（`required_roles` / `min_images` / `required_caps` …），
没有「按某个槽位字段查模板」的查询。拆成子表只会让每次保存草稿变成多表写，
而校验本来就必须在写入前由 `slots.Slots.parse` 做完——数据库约束无法跨
sqlite/postgres 表达同一套规则。

## 版本行存快照

`asset_template_versions.snapshot` 是冻结证据，只整块读。唯一约束
`(template_id, version)` 同时是「版本号不被覆盖」的保证（CAS），别删。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4c1a9e7d2b3"
down_revision: str | None = "e5b2c8d4f731"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "asset_templates",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reference_video_asset_id", sa.String(32), nullable=True),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("slots", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # `TimestampMixin.created_at` 自带 index=True，autogenerate 会要求它
    op.create_index("ix_asset_templates_created_at", "asset_templates", ["created_at"])
    op.create_index("ix_asset_templates_owner_id", "asset_templates", ["owner_id"])
    op.create_index("ix_asset_templates_kind", "asset_templates", ["kind"])
    op.create_index("ix_asset_templates_status", "asset_templates", ["status"])
    op.create_index("ix_asset_templates_video", "asset_templates", ["reference_video_asset_id"])
    # 两个部分索引：软删的行不进"我的模板"，也不进"可用模板"
    op.create_index(
        "ix_asset_templates_owner_active",
        "asset_templates",
        ["owner_id", "updated_at"],
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_asset_templates_public",
        "asset_templates",
        ["kind", "published_at"],
        sqlite_where=sa.text("status = 'published' AND deleted_at IS NULL"),
        postgresql_where=sa.text("status = 'published' AND deleted_at IS NULL"),
    )

    op.create_table(
        "asset_template_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("template_id", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("capability_note", sa.JSON(), nullable=False),
        sa.Column("published_by", sa.String(32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_asset_template_versions_created_at", "asset_template_versions",
                    ["created_at"])
    op.create_index("ix_asset_template_versions_template_id", "asset_template_versions",
                    ["template_id"])
    # 这条唯一索引是"版本号不被覆盖"的保证（CAS），别删
    op.create_index("uq_asset_template_versions_number", "asset_template_versions",
                    ["template_id", "version"], unique=True)

    op.create_table(
        "template_try_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("template_id", sa.String(32), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.String(32), nullable=True),
        sa.Column("result_asset_ids", sa.JSON(), nullable=False),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("cost_units", sa.Float(), nullable=False),
        sa.Column("created_by", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_template_try_runs_created_at", "template_try_runs", ["created_at"])
    op.create_index("ix_template_try_runs_template_id", "template_try_runs", ["template_id"])
    op.create_index("ix_template_try_runs_subject_id", "template_try_runs", ["subject_id"])
    op.create_index("ix_template_try_runs_recent", "template_try_runs",
                    ["template_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_template_try_runs_recent", table_name="template_try_runs")
    op.drop_index("ix_template_try_runs_subject_id", table_name="template_try_runs")
    op.drop_index("ix_template_try_runs_template_id", table_name="template_try_runs")
    op.drop_index("ix_template_try_runs_created_at", table_name="template_try_runs")
    op.drop_table("template_try_runs")

    op.drop_index("uq_asset_template_versions_number", table_name="asset_template_versions")
    op.drop_index("ix_asset_template_versions_template_id", table_name="asset_template_versions")
    op.drop_index("ix_asset_template_versions_created_at", table_name="asset_template_versions")
    op.drop_table("asset_template_versions")

    op.drop_index("ix_asset_templates_public", table_name="asset_templates")
    op.drop_index("ix_asset_templates_owner_active", table_name="asset_templates")
    op.drop_index("ix_asset_templates_video", table_name="asset_templates")
    op.drop_index("ix_asset_templates_status", table_name="asset_templates")
    op.drop_index("ix_asset_templates_kind", table_name="asset_templates")
    op.drop_index("ix_asset_templates_owner_id", table_name="asset_templates")
    op.drop_index("ix_asset_templates_created_at", table_name="asset_templates")
    op.drop_table("asset_templates")
