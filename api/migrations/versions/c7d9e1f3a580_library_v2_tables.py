"""library: 资产库 v2 的 10 张表

## 为什么新增而不是改 `assets`
这是第二版素材库，与现役 `/assets` **并行在线**。表、路由、权限、存储前缀
全部独立，切换完成后再下线旧库；回滚只需要删掉这 10 张表，不影响旧数据。

## 与模型的一致性
本文件与 `modules/library/models.py` 逐列对齐（`alembic check` 会比对）。
三条容易漏的：

- `library_assets.created_at` 有独立索引（来自 `TimestampMixin` 的 `index=True`）；
- `uq_library_assets_owner_sha_kind` 是**部分**唯一索引（`deleted_at IS NULL`），
  回收站里的旧行不能挡住「重新上传同一个文件」；
- `library_share_requests.group_id` 用空串而不是 NULL：部分唯一索引里 NULL
  互不相同，会让「同一资产两条 team pending」悄悄成立。

## 能否回滚
能。`downgrade()` 逆序删表；v2 与旧库没有外键，删表不会级联到 `assets`。
已上传的 v2 字节留在 `library/` 前缀下，需另跑清理（与「删模块不动旧库字节」一致）。

Revision ID: c7d9e1f3a580
Revises: b4e2d8f1a630
Create Date: 2026-11-09 21:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d9e1f3a580"
down_revision: str | Sequence[str] | None = "b4e2d8f1a630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_assets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_key", sa.String(length=256), nullable=False),
        sa.Column("cover_key", sa.String(length=256), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("trashed_from_state", sa.String(length=16), nullable=True),
        sa.Column("trash_reason", sa.String(length=20), nullable=True),
        sa.Column("trash_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=32), nullable=True),
        sa.Column("reviewed_by", sa.String(length=32), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.String(length=200), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("ref_count", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_library_assets_created_at", "library_assets", ["created_at"], unique=False
    )
    op.create_index(
        "ix_library_assets_owner_state_kind",
        "library_assets",
        ["owner_id", "state", "kind", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_library_assets_visibility_state_kind",
        "library_assets",
        ["visibility", "state", "kind", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_library_assets_trash_expiry",
        "library_assets",
        ["state", "trash_expires_at"],
        unique=False,
    )
    op.create_index(
        "uq_library_assets_owner_sha_kind",
        "library_assets",
        ["owner_id", "sha256", "kind"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL AND sha256 IS NOT NULL"),
        postgresql_where=sa.text("deleted_at IS NULL AND sha256 IS NOT NULL"),
    )
    op.create_index(
        "uq_library_assets_source_key",
        "library_assets",
        ["source_key"],
        unique=True,
        sqlite_where=sa.text("source_key IS NOT NULL"),
        postgresql_where=sa.text("source_key IS NOT NULL"),
    )

    op.create_table(
        "library_asset_groups",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_library_asset_groups",
        "library_asset_groups",
        ["asset_id", "group_id"],
        unique=True,
    )
    op.create_index(
        "ix_library_asset_groups_group", "library_asset_groups", ["group_id"], unique=False
    )

    op.create_table(
        "library_tags",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_library_tags_name", "library_tags", ["name"], unique=True)

    op.create_table(
        "library_asset_tags",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("tag_id", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_library_asset_tags",
        "library_asset_tags",
        ["asset_id", "tag_id"],
        unique=True,
    )
    op.create_index(
        "ix_library_asset_tags_tag", "library_asset_tags", ["tag_id"], unique=False
    )

    op.create_table(
        "library_pack_items",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("pack_id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_library_pack_items_position",
        "library_pack_items",
        ["pack_id", "position"],
        unique=True,
    )
    op.create_index(
        "ix_library_pack_items_asset", "library_pack_items", ["asset_id"], unique=False
    )

    op.create_table(
        "library_share_requests",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("applicant_id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("target", sa.String(length=10), nullable=False),
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("reviewer_id", sa.String(length=32), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_library_share_requests_pending",
        "library_share_requests",
        ["asset_id", "action", "target", "group_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_library_share_requests_applicant",
        "library_share_requests",
        ["applicant_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_library_share_requests_asset",
        "library_share_requests",
        ["asset_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "library_draft_reviews",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_library_draft_reviews_asset",
        "library_draft_reviews",
        ["asset_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "library_group_leaders",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_library_group_leaders",
        "library_group_leaders",
        ["group_id", "user_id"],
        unique=True,
    )
    op.create_index(
        "ix_library_group_leaders_user", "library_group_leaders", ["user_id"], unique=False
    )

    op.create_table(
        "library_asset_generation",
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("source_module", sa.String(length=20), nullable=False),
        sa.Column("source_task_id", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("model_id", sa.String(length=80), nullable=False),
        sa.Column("prompt", sa.String(length=4000), nullable=False),
        sa.Column("size", sa.String(length=32), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("style_preset", sa.String(length=40), nullable=False),
        sa.Column("seed", sa.String(length=64), nullable=False),
        sa.Column("reference_asset_ids", sa.JSON(), nullable=False),
        sa.Column("pack_id", sa.String(length=32), nullable=False),
        sa.Column("pack_version", sa.Integer(), nullable=False),
        sa.Column("prompt_spec", sa.JSON(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("credits", sa.Float(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("asset_id"),
    )

    op.create_table(
        "library_download_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("asset_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("storage_key", sa.String(length=256), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_library_download_jobs_created_at",
        "library_download_jobs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_library_download_jobs_owner",
        "library_download_jobs",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_library_download_jobs_expiry",
        "library_download_jobs",
        ["status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("library_download_jobs")
    op.drop_table("library_asset_generation")
    op.drop_table("library_group_leaders")
    op.drop_table("library_draft_reviews")
    op.drop_table("library_share_requests")
    op.drop_table("library_pack_items")
    op.drop_table("library_asset_tags")
    op.drop_table("library_tags")
    op.drop_table("library_asset_groups")
    op.drop_table("library_assets")
